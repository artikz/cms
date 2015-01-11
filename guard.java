import java.util.Scanner;
import java.util.Random;
import java.security.Permission;

class CMSSecurityManager extends SecurityManager {
    public void checkPermission(Permission perm) {
        // needed for file access
        // if (!(perm instanceof java.io.FilePermission) &&
        //     !(perm instanceof java.util.PropertyPermission) &&
        //     !(perm instanceof java.lang.RuntimePermission)) {
        //     super.checkPermission(perm);
        // }
    }    
    public void checkAccess(Thread g) {
        if ("solution".equals(Thread.currentThread().getName())) {
            throw new SecurityException("Threading is not allowed");
        }
    }
    public void checkAccess(ThreadGroup g) {
        if ("solution".equals(Thread.currentThread().getName())) {
            throw new SecurityException("Threading is not allowed");
        }
    }
}

class SolutionThread extends Thread {
	static Throwable t;
    String[] args;

    public SolutionThread(String[] args) {
        super("solution");
        this.args = args;
    }

    public void run() {
		try {
            System.setSecurityManager(new CMSSecurityManager());
            %SOLUTION_CLASS%.main(args);
		} catch (Throwable t) {
		    this.t = t;
		}
    }
}

public class guard {
    public static void main(String[] args) throws Throwable {
    	SolutionThread t = new SolutionThread(args);
    	t.start();
    	t.join();
    	if (SolutionThread.t != null) throw SolutionThread.t;
    }
}
