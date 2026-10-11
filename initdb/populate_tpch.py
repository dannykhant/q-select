import os
import time
import duckdb
import psycopg

DB_CONFIG = {
    "dbname": os.environ.get("PGDATABASE", "tpch_sf1"),
    "user": os.environ.get("PGUSER", "postgres"),
    "password": os.environ.get("PGPASSWORD", "postgres"),
    "host": os.environ.get("PGHOST", "localhost"),
    "port": int(os.environ.get("PGPORT", 5432)),
}

DDL_STATEMENTS = """
DROP TABLE IF EXISTS lineitem CASCADE;
DROP TABLE IF EXISTS orders CASCADE;
DROP TABLE IF EXISTS partsupp CASCADE;
DROP TABLE IF EXISTS part CASCADE;
DROP TABLE IF EXISTS supplier CASCADE;
DROP TABLE IF EXISTS customer CASCADE;
DROP TABLE IF EXISTS nation CASCADE;
DROP TABLE IF EXISTS region CASCADE;

CREATE TABLE region (
    r_regionkey INTEGER PRIMARY KEY,
    r_name CHAR(25) NOT NULL,
    r_comment VARCHAR(152)
);

CREATE TABLE nation (
    n_nationkey INTEGER PRIMARY KEY,
    n_name CHAR(25) NOT NULL,
    n_regionkey INTEGER NOT NULL REFERENCES region(r_regionkey),
    n_comment VARCHAR(152)
);

CREATE TABLE supplier (
    s_suppkey INTEGER PRIMARY KEY,
    s_name CHAR(25) NOT NULL,
    s_address VARCHAR(40) NOT NULL,
    s_nationkey INTEGER NOT NULL REFERENCES nation(n_nationkey),
    s_phone CHAR(15) NOT NULL,
    s_acctbal NUMERIC(15,2) NOT NULL,
    s_comment VARCHAR(101) NOT NULL
);

CREATE TABLE customer (
    c_custkey INTEGER PRIMARY KEY,
    c_name VARCHAR(25) NOT NULL,
    c_address VARCHAR(40) NOT NULL,
    c_nationkey INTEGER NOT NULL REFERENCES nation(n_nationkey),
    c_phone CHAR(15) NOT NULL,
    c_acctbal NUMERIC(15,2) NOT NULL,
    c_mktsegment CHAR(10) NOT NULL,
    c_comment VARCHAR(117) NOT NULL
);

CREATE TABLE part (
    p_partkey INTEGER PRIMARY KEY,
    p_name VARCHAR(55) NOT NULL,
    p_mfgr CHAR(25) NOT NULL,
    p_brand CHAR(10) NOT NULL,
    p_type VARCHAR(25) NOT NULL,
    p_size INTEGER NOT NULL,
    p_container CHAR(10) NOT NULL,
    p_retailprice NUMERIC(15,2) NOT NULL,
    p_comment VARCHAR(23) NOT NULL
);

CREATE TABLE partsupp (
    ps_partkey INTEGER NOT NULL REFERENCES part(p_partkey),
    ps_suppkey INTEGER NOT NULL REFERENCES supplier(s_suppkey),
    ps_availqty INTEGER NOT NULL,
    ps_supplycost NUMERIC(15,2) NOT NULL,
    ps_comment VARCHAR(199) NOT NULL,
    PRIMARY KEY (ps_partkey, ps_suppkey)
);

CREATE TABLE orders (
    o_orderkey BIGINT PRIMARY KEY,
    o_custkey INTEGER NOT NULL REFERENCES customer(c_custkey),
    o_orderstatus CHAR(1) NOT NULL,
    o_totalprice NUMERIC(15,2) NOT NULL,
    o_orderdate DATE NOT NULL,
    o_orderpriority CHAR(15) NOT NULL,
    o_clerk CHAR(15) NOT NULL,
    o_shippriority INTEGER NOT NULL,
    o_comment VARCHAR(79) NOT NULL
);

CREATE TABLE lineitem (
    l_orderkey BIGINT NOT NULL REFERENCES orders(o_orderkey),
    l_partkey INTEGER NOT NULL REFERENCES part(p_partkey),
    l_suppkey INTEGER NOT NULL REFERENCES supplier(s_suppkey),
    l_linenumber INTEGER NOT NULL,
    l_quantity NUMERIC(15,2) NOT NULL,
    l_extendedprice NUMERIC(15,2) NOT NULL,
    l_discount NUMERIC(15,2) NOT NULL,
    l_tax NUMERIC(15,2) NOT NULL,
    l_returnflag CHAR(1) NOT NULL,
    l_linestatus CHAR(1) NOT NULL,
    l_shipdate DATE NOT NULL,
    l_commitdate DATE NOT NULL,
    l_receiptdate DATE NOT NULL,
    l_shipinstruct CHAR(25) NOT NULL,
    l_shipmode CHAR(10) NOT NULL,
    l_comment VARCHAR(44) NOT NULL,
    PRIMARY KEY (l_orderkey, l_linenumber)
);

-- Indexes for join and filter patterns
CREATE INDEX idx_orders_custkey ON orders(o_custkey);
CREATE INDEX idx_orders_orderdate ON orders(o_orderdate);
CREATE INDEX idx_lineitem_orderkey ON lineitem(l_orderkey);
CREATE INDEX idx_lineitem_partkey ON lineitem(l_partkey);
CREATE INDEX idx_lineitem_suppkey ON lineitem(l_suppkey);
CREATE INDEX idx_lineitem_shipdate ON lineitem(l_shipdate);
CREATE INDEX idx_customer_nationkey ON customer(c_nationkey);
CREATE INDEX idx_customer_mktsegment ON customer(c_mktsegment);
CREATE INDEX idx_supplier_nationkey ON supplier(s_nationkey);
"""

TABLE_ORDER = [
    "region",
    "nation",
    "supplier",
    "customer",
    "part",
    "partsupp",
    "orders",
    "lineitem",
]

def populate_tpch(scale_factor: float = 1.0):
    print(f"🚀 Generating TPC-H SF {scale_factor} data in DuckDB in-memory...")
    t0 = time.time()
    ddb = duckdb.connect(":memory:")
    ddb.execute(f"CALL dbgen(sf={scale_factor});")
    print(f"✅ Data generated in DuckDB in {time.time() - t0:.2f}s")

    print(f"🔌 Connecting to PostgreSQL ({DB_CONFIG['dbname']} at {DB_CONFIG['host']}:{DB_CONFIG['port']})...")
    with psycopg.connect(**DB_CONFIG, autocommit=False) as conn:
        with conn.cursor() as cur:
            print("🏗️ Creating TPC-H tables and indexes...")
            cur.execute(DDL_STATEMENTS)
            conn.commit()

            print("📦 Streaming data from DuckDB to PostgreSQL via COPY...")
            for tbl in TABLE_ORDER:
                t_tbl = time.time()
                # Fetch columns from DuckDB
                cols = [col[0] for col in ddb.execute(f"DESCRIBE {tbl}").fetchall()]
                col_list = ", ".join(cols)
                
                # Stream via CSV buffer
                copy_sql = f"COPY {tbl} ({col_list}) FROM STDIN WITH (FORMAT CSV, HEADER FALSE)"
                
                # Export DuckDB table to CSV stream or write directly
                import io
                import tempfile
                import os
                
                with tempfile.NamedTemporaryFile(suffix=".csv", delete=False) as tmp_f:
                    tmp_path = tmp_f.name
                
                try:
                    ddb.execute(f"COPY {tbl} TO '{tmp_path}' (FORMAT CSV, HEADER FALSE, DATEFORMAT '%Y-%m-%d');")
                    with open(tmp_path, "r", encoding="utf-8") as f:
                        with cur.copy(copy_sql) as copy:
                            while chunk := f.read(65536):
                                copy.write(chunk)
                    conn.commit()
                finally:
                    if os.path.exists(tmp_path):
                        os.remove(tmp_path)
                
                cur.execute(f"SELECT count(*) FROM {tbl};")
                count = cur.fetchone()[0]
                print(f"   - {tbl}: {count:,} rows loaded in {time.time() - t_tbl:.2f}s")

            conn.commit()

    print("📊 Running VACUUM ANALYZE to update optimizer statistics...")
    with psycopg.connect(**DB_CONFIG, autocommit=True) as conn:
        with conn.cursor() as cur:
            cur.execute("VACUUM ANALYZE;")
    print("🎉 TPC-H SF 1.0 setup complete!")

if __name__ == "__main__":
    populate_tpch(scale_factor=1.0)
