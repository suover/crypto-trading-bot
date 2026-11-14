import java.io.Console;
import java.nio.charset.StandardCharsets;
import java.util.Arrays;
import org.springframework.security.crypto.bcrypt.BCrypt;

// Local-only helper. Passwords are never accepted as command-line arguments or saved.
class CreateAdminHash {
    public static void main(String[] args) {
        if (args.length == 1 && args[0].equals("--self-test")) {
            String password = "local-hash-helper-fixture-only";
            String hash = BCrypt.hashpw(password, BCrypt.gensalt(12));
            if (!hash.matches("\\$2[aby]\\$12\\$[./A-Za-z0-9]{53}")
                    || !BCrypt.checkpw(password, hash)
                    || BCrypt.checkpw(password + "wrong", hash)) {
                throw new IllegalStateException("BCrypt self-test failed");
            }
            System.out.println("PASS: BCrypt cost 12; correct password accepted; incorrect password rejected.");
            return;
        }
        if (args.length != 0) {
            throw new IllegalArgumentException("Do not pass passwords as arguments.");
        }
        Console console = System.console();
        if (console == null) {
            System.err.println("Open the IntelliJ Terminal or Windows Terminal and run this command there. Do not use the Run output panel.");
            System.exit(1);
        }
        char[] first = null;
        char[] second = null;
        byte[] passwordBytes = null;
        try {
            first = console.readPassword("New LOCAL dashboard administrator password (hidden): ");
            second = console.readPassword("Enter it again (hidden): ");
            if (first == null || second == null || first.length == 0 || !Arrays.equals(first, second)) {
                throw new IllegalArgumentException("Passwords must be non-empty and match. Run again.");
            }
            passwordBytes = new String(first).getBytes(StandardCharsets.UTF_8);
            if (passwordBytes.length > 72) {
                throw new IllegalArgumentException("BCrypt password must be at most 72 UTF-8 bytes. Run again.");
            }
            String hash = BCrypt.hashpw(passwordBytes, BCrypt.gensalt(12));
            if (!BCrypt.checkpw(passwordBytes, hash)) {
                throw new IllegalStateException("Generated hash verification failed");
            }
            console.printf("%nCopy this HASH to BACKEND_ADMIN_PASSWORD_HASH (no quotes):%n%s%n", hash);
            console.printf("Browser login uses your original password, not this hash.%n");
        } finally {
            if (first != null) Arrays.fill(first, '\0');
            if (second != null) Arrays.fill(second, '\0');
            if (passwordBytes != null) Arrays.fill(passwordBytes, (byte) 0);
        }
    }
}
