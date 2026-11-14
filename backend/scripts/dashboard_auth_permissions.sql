\set ON_ERROR_STOP on
BEGIN;
REVOKE ALL ON SCHEMA auth FROM PUBLIC;
REVOKE ALL ON auth.accounts FROM PUBLIC;
GRANT USAGE ON SCHEMA auth TO backend_reader, dashboard_auth_writer;
GRANT SELECT ON auth.accounts TO backend_reader;
GRANT CONNECT ON DATABASE :"DBNAME" TO dashboard_auth_writer;
GRANT INSERT (username, password_hash, trading_user_id)
    ON auth.accounts TO dashboard_auth_writer;
COMMIT;
