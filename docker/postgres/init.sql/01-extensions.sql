-- docker/postgres/init.sql/01-extensions.sql
-- 首次启动自动执行 (PG 官方 entrypoint 会按文件名顺序执行 *.sql).
-- 当前 SQLAlchemy 用到的类型 (JSONB / UUID) 都是 PG 内置, 不需要扩;
-- 这里加 pgcrypto 给以后 ``gen_random_uuid()`` / digest() 备用.
--
-- 如果想在多个 schema 上分发扩展, 在 create extension 之前 ``SET search_path = ...``;

CREATE EXTENSION IF NOT EXISTS pgcrypto;
-- CREATE EXTENSION IF NOT EXISTS "uuid-ossp";   -- uuid-ossp 与 SQLAlchemy 默认 ``uuid_generate_v4``; 用 PG 13+ 的 gen_random_uuid() 即可
-- CREATE EXTENSION IF NOT EXISTS pg_trgm;        -- 模糊搜索, 目前未用
