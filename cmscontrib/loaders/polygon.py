#!/usr/bin/env python2
# -*- coding: utf-8 -*-

# Programming contest management system
# Copyright © 2014 Artem Iglikov <artem.iglikov@gmail.com>
# Copyright © 2014 Stefano Maggiolo <s.maggiolo@gmail.com>
#
# This program is free software: you can redistribute it and/or modify
# it under the terms of the GNU Affero General Public License as
# published by the Free Software Foundation, either version 3 of the
# License, or (at your option) any later version.
#
# This program is distributed in the hope that it will be useful,
# but WITHOUT ANY WARRANTY; without even the implied warranty of
# MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.  See the
# GNU Affero General Public License for more details.
#
# You should have received a copy of the GNU Affero General Public License
# along with this program.  If not, see <http://www.gnu.org/licenses/>.

from __future__ import absolute_import
from __future__ import print_function
from __future__ import unicode_literals

import json
import io
import json
import logging
import os
import sys

from datetime import datetime
from datetime import timedelta

import xml.etree.ElementTree as ET

from cms import config
from cms.db import Contest, User, Task, Statement, \
    SubmissionFormatElement, Dataset, Manager, Testcase
from cmscontrib import touch

from .base_loader import ContestLoader, TaskLoader, UserLoader

logger = logging.getLogger(__name__)


def make_timedelta(t):
    return timedelta(seconds=t)


# TODO: add all languages.
LANGUAGE_MAP = {'english': 'en', 'russian': 'ru', 'italian': 'it'}


class PolygonTaskLoader(TaskLoader):
    """Load a task stored using the Codeforces Polygon format.

    Given the filesystem location of a unpacked task that was packaged
    in the Polygon format (tests should already be generated), parse
    those files and directories to produce data that can be consumed by
    CMS, i.e. a Task object

    Also, as Polygon doesn't support CMS directly, and doesn't allow
    to customize some task parameters, users can add task configuration
    files which will be parsed and applied as is. By default, all tasks
    are batch files, with custom checker and score type is Sum.

    Loaders assumes that checker is check.cpp and written with usage of
    testlib.h. It provides customized version of testlib.h which allows
    using Polygon checkers with CMS. Checkers will be compiled during
    importing the contest.

    """

    short_name = 'polygon_task'
    description = 'Polygon (XML-based) task format'

    @staticmethod
    def detect(path):
        """See docstring in class Loader.

        """
        return os.path.exists(os.path.join(path, "problem.xml"))

    def task_has_changed(self):
        """See docstring in class Loader.

        """
        return True

    def get_task(self, get_statement=True):
        """See docstring in class Loader.

        """

        logger.info("Checking dos2unix presence")
        i = os.system('dos2unix -V 2>/dev/null')
        self.dos2unix_found = (i == 0)
        if not self.dos2unix_found:
            logger.error("dos2unix not found - tests will not be converted!")

        name = os.path.basename(self.path)
        logger.info("Loading parameters for task %s.", name)

        args = {}

        # Here we update the time of the last import.
        touch(os.path.join(self.path, ".itime"))
        # If this file is not deleted, then the import failed.
        touch(os.path.join(self.path, ".import_error"))

        # Get alphabetical task index for use in title.

        tree = ET.parse(os.path.join(self.path, "problem.xml"))
        root = tree.getroot()

        args["name"] = name
        args["title"] = root.find('names').find("name").attrib['value']

        if get_statement:
            args["statements"] = []
            args["primary_statements"] = []
            for language, language_code in LANGUAGE_MAP.iteritems():
                path = os.path.join(self.path, 'statements',
                                    '.pdf', language, 'problem.pdf')
                if os.path.exists(path):
                    lang = LANGUAGE_MAP[language]
                    digest = self.file_cacher.put_file_from_path(
                        path,
                        "Statement for task %s (lang: %s)" % (name,
                                                              language))
                    args["statements"].append(Statement(lang, digest))
                    args["primary_statements"].append(lang)
            args["primary_statements"] = json.dumps(args["primary_statements"])

        args["submission_format"] = [SubmissionFormatElement("%s.%%l" % name)]

        # These options cannot be configured in the Polygon format.
        # Uncomment the following to set specific values for them.

        # args['max_submission_number'] = 100
        # args['max_user_test_number'] = 100
        # args['min_submission_interval'] = make_timedelta(60)
        # args['min_user_test_interval'] = make_timedelta(60)

        # args['max_user_test_number'] = 10
        # args['min_user_test_interval'] = make_timedelta(60)

        # args['token_mode'] = 'infinite'
        # args['token_max_number'] = 100
        # args['token_min_interval'] = make_timedelta(60)
        # args['token_gen_initial'] = 1
        # args['token_gen_number'] = 1
        # args['token_gen_interval'] = make_timedelta(1800)
        # args['token_gen_max'] = 2

        task_cms_conf_path = os.path.join(self.path, 'files')
        task_cms_conf = None
        if os.path.exists(os.path.join(task_cms_conf_path, 'cms_conf.py')):
            sys.path.append(task_cms_conf_path)
            logger.info("Found additional CMS options for task %s.", name)
            task_cms_conf = __import__('cms_conf')
            # TODO: probably should find more clever way to get rid of caching
            task_cms_conf = reload(task_cms_conf)
            sys.path.pop()
        if task_cms_conf is not None and hasattr(task_cms_conf, "general"):
            args.update(task_cms_conf.general)

        print(task_cms_conf.datasets)
        task = Task(**args)

        self.task = task

        judging = root.find('judging')

        # Though some parameters are coming from a testset, they actually
        # the same in all testsets
        some_testset = judging[0]

        active_dataset_name = None

        dataset_default_args = {}
        dataset_default_args["task"] = task
        dataset_default_args["autojudge"] = False
        tl = float(some_testset.find('time-limit').text)
        ml = float(some_testset.find('memory-limit').text)
        dataset_default_args["time_limit"] = tl * 0.001
        dataset_default_args["memory_limit"] = int(ml / (1024 * 1024))
        infile_param = judging.attrib['input-file']
        outfile_param = judging.attrib['output-file']

        checker_src = os.path.join(self.path, "files", "check.cpp")
        checker_exe = None
        if os.path.exists(checker_src):
            logger.info("Checker found, compiling")
            checker_exe = os.path.join(self.path, "files", "checker")
            testlib_path = "/usr/local/include/cms/testlib.h"
            if not config.installed:
                testlib_path = os.path.join(os.path.dirname(__file__),
                                            "polygon", "testlib.h")
            os.system("cat %s | \
                sed 's$testlib.h$%s$' | \
                g++ -x c++ -O2 -static -o %s -" %
                      (checker_src, testlib_path, checker_exe))
            evaluation_param = "comparator"
        else:
            logger.info("Checker not found, using diff")
            evaluation_param = "diff"

        dataset_default_args["task_type"] = "Batch"
        compilation_type = "alone"
        if task_cms_conf is not None and \
           hasattr(task_cms_conf, "sources") and \
           len(task_cms_conf.sources) > 0:
            compilation_type = "grader"

        dataset_default_args["task_type_parameters"] = \
            '["%s", ["%s", "%s"], "%s"]' % \
            (compilation_type, infile_param, outfile_param, evaluation_param)

        dataset_default_args["score_type"] = "Sum"

        testsets = {}
        datasets_auto = {}
        for testset in judging:
            testset_name = testset.attrib["name"].lower()
            testsets[testset_name] = testset

            if not active_dataset_name or testset_name == "tests":
                active_dataset_name = testset_name

            args = dataset_default_args.copy()
            args["description"] = testset_name
            args["managers"] = []
            args["testcases"] = []
            args["polygon_auto"] = True
            args["polygon_testset"] = testset_name

            total_value = 100.0
            input_value = 0.0

            n_testcases = int(testset.find('test-count').text)

            if n_testcases != 0:
                input_value = total_value / n_testcases
            args["score_type_parameters"] = str(input_value)

            datasets_auto[testset_name] = args

        print("auto %s" % repr(datasets_auto))
        used_testsets = set()

        datasets = {}

        if task_cms_conf is not None and \
           hasattr(task_cms_conf, "datasets"):
            # If there is a manual dataset, it should be used as active one
            active_dataset_name = None
            for ds_name, ds_args in task_cms_conf.datasets.iteritems():
                if not active_dataset_name or ds_name == "tests":
                    active_dataset_name = ds_name
                args = dataset_default_args.copy()
                if ds_name in datasets_auto:
                    args = datasets_auto[ds_name]
                    used_testsets.add(ds_name)
                else:
                    args["description"] = ds_name
                    args["managers"] = []
                    args["testcases"] = []
                    args["polygon_testset"] = "tests"

                    n_testcases = int(testset.find('test-count').text)

                    if n_testcases != 0:
                        input_value = total_value / n_testcases
                    args["score_type_parameters"] = str(input_value)

                args["polygon_auto"] = False
                args.update(ds_args)

                datasets[ds_name] = args;

        print("other %s" % repr(datasets))

        datasets_list = datasets.keys()
        datasets_list += datasets_auto.keys()
        datasets_auto.update(datasets)
        datasets = datasets_auto

        for ds_name in datasets_list:
            ds_args = datasets[ds_name]
            if "polygon_auto" not in ds_args or \
               (ds_args["polygon_auto"] and ds_name in used_testsets):
                continue
            print("dataset %s" % ds_name)
            print(ds_args)
            if isinstance(ds_args["score_type_parameters"], list):
                for subtask in ds_args["score_type_parameters"]:
                    start_testcase = len(ds_args["testcases"]) + 1
                    ignore_testcases = False
                    if "polygon_testsets" in subtask:
                        for ts_name in subtask["polygon_testsets"]:
                            ts_name = ts_name.lower()
                            self.add_testset_to_dataset(testsets[ts_name],
                                ds_args)
                            used_testsets.add(ts_name)
                        del subtask["polygon_testsets"]
                        if "polygon_testset" in subtask:
                            logger.warn("\"polygon_testset\" is ignored " \
                                "in dataset %s", ds_name)
                        if "polygon_testcases" in subtask:
                            logger.warn("\"polygon_testcases\" is ignored " \
                                "in dataset %s", ds_name)
                        ignore_testcases = True
                    else:
                        ts_name = ds_args["polygon_testset"]
                        if "polygon_testset" in subtask:
                            ts_name = subtask["polygon_testset"].lower()
                            del subtask["polygon_testset"]
                            ignore_testcases = True
                        testcases_to_use = None
                        if "polygon_testcases" in subtask:
                            testcases_to_use = subtask["polygon_testcases"]
                            del subtask["polygon_testcases"]
                            ignore_testcases = True
                        used_testsets.add(ts_name)
                        if ignore_testcases or start_testcase == 1:
                            self.add_testset_to_dataset(testsets[ts_name],
                                ds_args, testcases_to_use)
                    end_testcase = len(ds_args["testcases"])
                    if "testcases" not in subtask:
                        subtask["testcases"] = [(start_testcase, end_testcase)]
                    elif ignore_testcases:
                        logger.warn("\"testcases\" is ignored " \
                            "in dataset %s", ds_name)
                ds_args["score_type_parameters"] = json.dumps(ds_args["score_type_parameters"])
            else:
                self.add_testset_to_dataset(testsets[ds_name], ds_args)
            if checker_exe:
                digest = self.file_cacher.put_file_from_path(
                    checker_exe,
                    "Manager for task %s" % name)
                ds_args["managers"] += [
                    Manager("checker", digest)]

            if task_cms_conf is not None and \
               hasattr(task_cms_conf, "sources"):
                for filename in task_cms_conf.sources:
                    filepath = os.path.join(self.path, "files", filename)
                    digest = self.file_cacher.put_file_from_path(
                        filepath,
                        "%s - additional file for task %s" % (filename, name))
                    ds_args["managers"] += [
                        Manager(filename, digest)]

            del ds_args["polygon_testset"]
            del ds_args["polygon_auto"]
            print(ds_args)
            dataset = Dataset(**ds_args)
            if ds_name == active_dataset_name:
                task.active_dataset = dataset

            print(task.datasets)

            # testcases = []

            # for i in xrange(n_testcases):
            #     infile = os.path.join(self.path, testset_name,
            #                           "%02d" % (i + 1))
            #     outfile = os.path.join(self.path, testset_name,
            #                            "%02d.a" % (i + 1))
            #     if self.dos2unix_found:
            #         os.system('dos2unix -q %s' % (infile, ))
            #         os.system('dos2unix -q %s' % (outfile, ))
            #     input_digest = self.file_cacher.put_file_from_path(
            #         infile,
            #         "Input %d for task %s" % (i, name))
            #     output_digest = self.file_cacher.put_file_from_path(
            #         outfile,
            #         "Output %d for task %s" % (i, name))
            #     testcase = Testcase("%03d" % (i, ), False,
            #                         input_digest, output_digest)
            #     testcase.public = True
            #     testcases += [testcase]

            # testsets[testset_name] = testcases




        os.remove(os.path.join(self.path, ".import_error"))

        logger.info("Task parameters loaded.")
        return task

    def add_testset_to_dataset(self, testset, ds_args, testcases_to_use=None):
        if testcases_to_use:
            testcases_to_use = self.testcases_indices_from_intervals(testcases_to_use)
        testset_name = testset.attrib["name"]
        n_testcases = int(testset.find('test-count').text)
        start_testcase_index = len(ds_args["testcases"])
        testcases = []
        for i in xrange(n_testcases):
            if testcases_to_use and i not in testcases_to_use:
                continue
            cms_i = start_testcase_index + i
            print("testcase %d" % cms_i)
            infile = os.path.join(self.path, testset_name,
                                  "%02d" % (i + 1))
            outfile = os.path.join(self.path, testset_name,
                                   "%02d.a" % (i + 1))
            if self.dos2unix_found:
                os.system('dos2unix -q %s' % (infile, ))
                os.system('dos2unix -q %s' % (outfile, ))
            input_digest = self.file_cacher.put_file_from_path(
                infile,
                "Input %d for task %s" % (cms_i, self.task.name))
            output_digest = self.file_cacher.put_file_from_path(
                outfile,
                "Output %d for task %s" % (cms_i, self.task.name))
            testcase = Testcase("%03d" % cms_i, False,
                                input_digest, output_digest)
            testcase.public = True
            testcases += [testcase]
        ds_args["testcases"] += testcases

    def testcases_indices_from_intervals(self, testcases_intervals):
        testcases = set()
        for tc in testcases_intervals:
            if isinstance(tc, int):
                testcases.add(tc)
            else:
                for tc_idx in xrange(tc[0], tc[1] + 1):
                    testcases.add(tc_idx)
        return testcases


class PolygonUserLoader(UserLoader):
    """Load a user stored using the Codeforces Polygon format.

    As Polygon doesn't support CMS directly, and doesn't allow
    to specify users, we support(?) a non-standard file named
    contestants.txt to allow importing some set of users.

    """

    short_name = 'polygon_user'
    description = 'Polygon (XML-based) user format'

    @staticmethod
    def detect(path):
        """See docstring in class Loader.

        """
        return os.path.exists(
            os.path.join(os.path.dirname(path), "contestants.txt"))

    def user_has_changed(self):
        """See docstring in class Loader.

        """
        return True

    def get_user(self):
        """See docstring in class Loader.

        """

        username = os.path.basename(self.path)
        userdata = None

        # This is not standard Polygon feature, but useful for CMS users
        # we assume contestants.txt contains one line for each user:
        #
        # username;password;first_name;last_name;hidden
        #
        # For example:
        #
        # contestant1;123;Cont;Estant;0
        # jury;1234;Ju;Ry;1

        users_path = os.path.join(
            os.path.dirname(self.path), 'contestants.txt')
        if os.path.exists(users_path):
            with io.open(users_path, "rt", encoding="utf-8") as users_file:
                for user in users_file.readlines():
                    user = user.strip().split(';')
                    name = user[0].strip()
                    if name == username:
                        userdata = [x.strip() for x in user]

        if userdata is not None:
            logger.info("Loading parameters for user %s.", username)
            args = {}
            args['username'] = userdata[0]
            args['password'] = userdata[1]
            args['first_name'] = userdata[2]
            args['last_name'] = userdata[3]
            args['hidden'] = (len(userdata) > 4 and userdata[4] == '1')
            logger.info("User parameters loaded.")
            return User(**args)
        else:
            logger.critical(
                "User %s not found in contestants.txt file.", username)
            return None


class PolygonContestLoader(ContestLoader):
    """Load a contest stored using the Codeforces Polygon format.

    Given the filesystem location of a unpacked package of contest in
    the Polygon format, parse those files and directories to produce
    data that can be consumed by CMS, i.e. a Contest object.

    Polygon (by now) doesn't allow custom contest-wide files, so
    general contest options should be hard-coded in the loader.

    """

    short_name = 'polygon_contest'
    description = 'Polygon (XML-based) contest format'

    @staticmethod
    def detect(path):
        """See docstring in class Loader.

        """
        return os.path.exists(os.path.join(path, "contest.xml")) and \
            os.path.exists(os.path.join(path, "problems"))

    def get_task_loader(self, taskname):
        taskpath = os.path.join(self.path, "problems", taskname)
        return PolygonTaskLoader(taskpath, self.file_cacher)

    def get_contest(self):
        """See docstring in class Loader.

        """

        name = os.path.split(self.path)[1]

        logger.info("Loading parameters for contest %s.", name)

        args = {}

        tree = ET.parse(os.path.join(self.path, "contest.xml"))
        root = tree.getroot()

        args['name'] = name

        # TODO: find proper way to choose contest primary language.

        self.primary_language = root.find('names') \
            .find('name').attrib['language']

        # All available contest languages are allowed to be used.

        self.languages = []
        for alternative_name in root.find('names'):
            self.languages.append(alternative_name.attrib['language'])

        logger.info("Contest languages are %s %s",
                    self.primary_language, str(self.languages))

        args['description'] = root.find('names') \
            .find("name[@language='%s']" % self.primary_language) \
            .attrib['value']

        logger.info("Contest description is %s", args['description'])

        # For now Polygon doesn't support custom contest-wide files,
        # so we need to hardcode some contest settings.

        args['start'] = datetime(1970, 1, 1)
        args['stop'] = datetime(1970, 1, 1)

        # Uncomment the following to set specific values for these
        # options.

        # args['max_submission_number'] = 100
        # args['max_user_test_number'] = 100
        # args['min_submission_interval'] = make_timedelta(60)
        # args['min_user_test_interval'] = make_timedelta(60)
        # args['max_user_test_number'] = 10
        # args['min_user_test_interval'] = make_timedelta(60)

        # args['token_mode'] = 'infinite'
        # args['token_max_number'] = 100
        # args['token_min_interval'] = make_timedelta(60)
        # args['token_gen_initial'] = 1
        # args['token_gen_number'] = 1
        # args['token_gen_interval'] = make_timedelta(1800)
        # args['token_gen_max'] = 2

        logger.info("Contest parameters loaded.")

        tasks = []
        for problem in root.find('problems'):
            tasks.append(os.path.basename(problem.attrib['url']))

        users = []

        # This is not standard Polygon feature, but useful for CMS users
        # we assume contestants.txt contains one line for each user:
        #
        # username;password;first_name;last_name;hidden
        #
        # For example:
        #
        # contestant1;123;Cont;Estant;0
        # jury;1234;Ju;Ry;1

        users_path = os.path.join(self.path, 'contestants.txt')
        if os.path.exists(users_path):
            with io.open(users_path, "rt", encoding="utf-8") as users_file:
                for user in users_file.readlines():
                    user = user.strip()
                    user = user.split(';')
                    username = user[0].strip()
                    users.append(username)

        return Contest(**args), tasks, users

    def contest_has_changed(self):
        """See docstring in class Loader.

        """
        return True
