-- Security Advisor: "Function Search Path Mutable" warnings on 6 functions.
-- Exact signatures confirmed live via pg_get_function_identity_arguments
-- before writing this (not guessed) — see item 12 report. Note:
-- update_updated_at_column() also exists in the `storage` schema
-- (Supabase-managed) — intentionally NOT touched here, only the
-- application-owned `public` copy.

ALTER FUNCTION public.update_updated_at_column() SET search_path = public;

ALTER FUNCTION public.match_products(vector, double precision, integer)
  SET search_path = public;

ALTER FUNCTION public.match_rag_chunks(vector, double precision, integer, jsonb)
  SET search_path = public;

ALTER FUNCTION public.match_tenant_rag_chunks(uuid, vector, double precision, integer)
  SET search_path = public;

ALTER FUNCTION public.replace_refund_policy_excluded_products(uuid, bigint[])
  SET search_path = public;

ALTER FUNCTION public.replace_refund_policy_excluded_collections(uuid, bigint[])
  SET search_path = public;
