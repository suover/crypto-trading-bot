import java.io.Console;
import java.nio.charset.StandardCharsets;
import java.sql.Connection;
import java.sql.DriverManager;
import java.sql.SQLException;
import java.util.Arrays;
import org.springframework.security.crypto.bcrypt.BCrypt;

/** Operator CLI. The backend process only reads account rows. */
class CreateDashboardAccount {
    public static void main(String[] args) {
        if (args.length != 1 || !args[0].matches("[1-9][0-9]*")) {
            System.err.println("Usage: CreateDashboardAccount <trading-user-id>");
            System.exit(1);
        }
        Console console = System.console();
        if (console == null) {
            System.err.println("Run this command in an interactive terminal.");
            System.exit(1);
        }

        try {
            create(console, Long.parseLong(args[0]));
            console.printf("Dashboard account created. You can sign in now.%n");
        } catch (SQLException e) {
            if ("23503".equals(e.getSQLState())) {
                System.err.println("The linked trading user does not exist.");
            } else if ("23505".equals(e.getSQLState())) {
                System.err.println("That dashboard username already exists.");
            } else {
                System.err.println("Account creation failed. Verify database access and permissions.");
            }
            System.exit(1);
        } catch (IllegalArgumentException e) {
            System.err.println("Account creation failed. Verify database access, the trading user, and username availability.");
            System.exit(1);
        }
    }

    private static void create(Console console, long tradingUserId) throws SQLException {
        String username = console.readLine("Dashboard username: ");
        if (username == null || !username.matches("[A-Za-z0-9][A-Za-z0-9._-]{0,127}")) {
            throw new IllegalArgumentException("Invalid username");
        }
        String url = System.getenv("BACKEND_DB_URL");
        if (url == null || !url.startsWith("jdbc:postgresql://")
                || url.matches("(?i).*([?&])(user|password)=.*")) {
            throw new IllegalArgumentException("Missing database URL");
        }
        try (var trading = connect(console, url, "BACKEND_DB")) {
            trading.setReadOnly(true);
            validateTradingUser(trading, tradingUserId);
        }
        try (var auth = connect(console, url, "DASHBOARD_AUTH_WRITER")) {
            String passwordHash = readHash(console);
            try (var insert = auth.prepareStatement("""
                    INSERT INTO auth.accounts(username, password_hash, trading_user_id)
                    VALUES (?, ?, ?)
                    """)) {
                insert.setString(1, username);
                insert.setString(2, passwordHash);
                insert.setLong(3, tradingUserId);
                insert.executeUpdate();
            }
        }
    }

    private static Connection connect(Console console, String url, String prefix) throws SQLException {
        String username = System.getenv(prefix + "_USERNAME");
        if (username == null || username.isBlank()) {
            throw new IllegalArgumentException("Missing database settings");
        }
        char[] password = console.readPassword("%s database password: ", prefix);
        try {
            if (password == null || password.length == 0) {
                throw new IllegalArgumentException("Missing password");
            }
            return DriverManager.getConnection(url, username, new String(password));
        } finally {
            if (password != null) Arrays.fill(password, '\0');
        }
    }

    private static void validateTradingUser(Connection trading, long id) throws SQLException {
        try (var query = trading.prepareStatement("SELECT id FROM public.users WHERE id = ? AND is_active")) {
            query.setLong(1, id);
            try (var result = query.executeQuery()) {
                if (!result.next()) throw new IllegalArgumentException("Trading user is unavailable");
            }
        }
    }

    private static String readHash(Console console) {
        char[] first = console.readPassword("New dashboard password (hidden): ");
        char[] second = null;
        byte[] bytes = null;
        try {
            second = console.readPassword("Repeat dashboard password (hidden): ");
            if (first == null || first.length == 0 || !Arrays.equals(first, second)) {
                throw new IllegalArgumentException("Passwords do not match");
            }
            bytes = new String(first).getBytes(StandardCharsets.UTF_8);
            if (bytes.length > 72) throw new IllegalArgumentException("BCrypt supports at most 72 UTF-8 bytes");
            return BCrypt.hashpw(bytes, BCrypt.gensalt(12));
        } finally {
            if (first != null) Arrays.fill(first, '\0');
            if (second != null) Arrays.fill(second, '\0');
            if (bytes != null) Arrays.fill(bytes, (byte) 0);
        }
    }
}
