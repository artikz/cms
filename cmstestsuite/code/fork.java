import java.util.Scanner;

public class batchstdio {
  public static void main(String[] args) {
    new Thread() {
      public void run() {
        Scanner in = new Scanner(System.in);
        int n = in.nextInt();
        System.out.println("correct " + n);
      }
    }.start();
  }
}
