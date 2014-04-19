#!/usr/bin/env python2
# -*- coding: utf-8 -*-

# Programming contest management system
# Copyright © 2010-2012 Giovanni Mascellani <mascellani@poisson.phc.unipi.it>
# Copyright © 2010-2012 Stefano Maggiolo <s.maggiolo@gmail.com>
# Copyright © 2010-2012 Matteo Boscariol <boscarim@hotmail.com>
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

from cms.grading.ScoreType import ScoreTypeGroup


# Dummy function to mark translatable string.
def N_(message):
    return message


class GroupMin(ScoreTypeGroup):
    """The score of a submission is the sum of the product of the
    minimum of the ranges with the multiplier of that range.

    Parameters are [[m, t], ... ] (see ScoreTypeGroup).

    """

    def get_public_outcome(self, outcome, parameter):
        """See ScoreTypeGroup."""
        if outcome <= 0.0:
            return N_("Not correct")
        elif outcome >= 1.0:
            return N_("Correct")
        else:
            return N_("Partially correct")

    def reduce(self, outcomes, subtasks_scores, parameter):
        """See ScoreTypeGroup."""
        if subtasks_scores and len(parameter) >= 4:
            for i in parameter[3]:
                if subtasks_scores[i - 1] <= 0.0:
                    return 0;
        return min(outcomes)

    def is_score_already_known(self, known_testcases_outcomes, known_subtasks_scores, parameter):
        # Check, whether a subtask we depend on is failed.
        if known_subtasks_scores and len(parameter) >= 4:
            for i in parameter[3]:
                if known_subtasks_scores[i - 1] <= 0.0:
                    return True;
        # If no dependent subtasks failed, check whether there are failed tests.
        if len(parameter) < 3 or parameter[2] == 0:
            return False
        if not known_testcases_outcomes:
            return False
        if min(known_testcases_outcomes) <= 0.0:
            return True
        return False
