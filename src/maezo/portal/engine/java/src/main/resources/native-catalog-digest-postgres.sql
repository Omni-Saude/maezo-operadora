WITH p AS (SELECT ?::name AS schema, string_to_array(?, ',')::name[] AS tables, ?::name AS owner, ?::name AS runtime),
t AS (SELECT c.oid, c.relname, c.relkind, c.relrowsecurity, c.relforcerowsecurity, c.relowner, c.relacl
      FROM pg_catalog.pg_class c JOIN pg_catalog.pg_namespace n ON n.oid = c.relnamespace, p
      WHERE n.nspname = p.schema AND c.relname = ANY(p.tables)),
r AS (SELECT o.oid, CASE WHEN o.oid = 0 THEN 'PUBLIC' WHEN o.rolname = p.owner THEN 'owner'
                         WHEN o.rolname = p.runtime THEN 'runtime' ELSE o.rolname::text END AS label
      FROM (SELECT 0::oid AS oid, ''::name AS rolname UNION ALL SELECT oid, rolname FROM pg_catalog.pg_roles) o, p),
line AS (
 SELECT pg_catalog.format('rel|%s|%s|%s|%s|%s', t.relname, t.relkind, t.relrowsecurity, t.relforcerowsecurity,
        (SELECT label FROM r WHERE r.oid = t.relowner)) AS v FROM t
 UNION ALL
 SELECT pg_catalog.format('col|%s|%s|%s|%s|%s|%s', t.relname, a.attnum, a.attname,
        pg_catalog.format_type(a.atttypid, a.atttypmod), a.attnotnull,
        COALESCE(pg_catalog.pg_get_expr(d.adbin, d.adrelid), ''))
 FROM t JOIN pg_catalog.pg_attribute a ON a.attrelid = t.oid AND a.attnum > 0 AND NOT a.attisdropped
 LEFT JOIN pg_catalog.pg_attrdef d ON d.adrelid = t.oid AND d.adnum = a.attnum
 UNION ALL
 SELECT pg_catalog.format('con|%s|%s|%s|%s', t.relname, k.conname, k.contype,
        pg_catalog.replace(pg_catalog.pg_get_constraintdef(k.oid), pg_catalog.quote_ident(p.schema) || '.', ''))
 FROM t JOIN pg_catalog.pg_constraint k ON k.conrelid = t.oid, p
 UNION ALL
 SELECT pg_catalog.format('idx|%s|%s', t.relname,
        pg_catalog.replace(pg_catalog.pg_get_indexdef(i.indexrelid), pg_catalog.quote_ident(p.schema) || '.', ''))
 FROM t JOIN pg_catalog.pg_index i ON i.indrelid = t.oid, p
 UNION ALL
 SELECT pg_catalog.format('acl|%s|%s|%s|%s|%s', t.relname, ge.label, gr.label, x.privilege_type, x.is_grantable)
 FROM t CROSS JOIN LATERAL pg_catalog.aclexplode(COALESCE(t.relacl, pg_catalog.acldefault('r', t.relowner))) x
 JOIN r ge ON ge.oid = x.grantee JOIN r gr ON gr.oid = x.grantor
 UNION ALL
 SELECT pg_catalog.format('colacl|%s|%s|%s|%s|%s|%s', t.relname, a.attname, ge.label, gr.label, x.privilege_type, x.is_grantable)
 FROM t JOIN pg_catalog.pg_attribute a ON a.attrelid = t.oid AND a.attacl IS NOT NULL
 CROSS JOIN LATERAL pg_catalog.aclexplode(a.attacl) x
 JOIN r ge ON ge.oid = x.grantee JOIN r gr ON gr.oid = x.grantor
 UNION ALL
 SELECT pg_catalog.format('trg|%s|%s|%s|%s|%s|%s|%s|%s|%s|%s|%s', t.relname, g.tgname, g.tgtype, g.tgenabled,
        f.proname, f.prosecdef, (SELECT label FROM r WHERE r.oid = f.proowner),
        CASE WHEN f.pronamespace = (SELECT oid FROM pg_catalog.pg_namespace WHERE nspname = p.schema)
             THEN 'schema' ELSE pg_catalog.format('%s', f.pronamespace::regnamespace) END,
        COALESCE((SELECT pg_catalog.string_agg(pg_catalog.format('%s:%s:%s:%s', ge.label, gr.label, x.privilege_type,
                  x.is_grantable), ',' ORDER BY ge.label, x.privilege_type)
                  FROM pg_catalog.aclexplode(COALESCE(f.proacl, pg_catalog.acldefault('f', f.proowner))) x
                  JOIN r ge ON ge.oid = x.grantee JOIN r gr ON gr.oid = x.grantor), ''),
        pg_catalog.md5(pg_catalog.replace(pg_catalog.pg_get_functiondef(f.oid), pg_catalog.quote_ident(p.schema) || '.', '')),
        pg_catalog.replace(pg_catalog.pg_get_triggerdef(g.oid), pg_catalog.quote_ident(p.schema) || '.', ''))
 FROM t JOIN pg_catalog.pg_trigger g ON g.tgrelid = t.oid AND NOT g.tgisinternal
 JOIN pg_catalog.pg_proc f ON f.oid = g.tgfoid, p
 UNION ALL
 SELECT pg_catalog.format('pol|%s|%s|%s|%s|%s|%s|%s', t.relname, y.polname, y.polcmd, y.polpermissive,
        (SELECT pg_catalog.string_agg(ge.label, ',' ORDER BY ge.label) FROM r ge WHERE ge.oid = ANY(y.polroles)),
        COALESCE(pg_catalog.pg_get_expr(y.polqual, y.polrelid), ''),
        COALESCE(pg_catalog.pg_get_expr(y.polwithcheck, y.polrelid), ''))
 FROM t JOIN pg_catalog.pg_policy y ON y.polrelid = t.oid
 UNION ALL
 SELECT pg_catalog.format('rule|%s|%s', t.relname, w.rulename)
 FROM t JOIN pg_catalog.pg_rewrite w ON w.ev_class = t.oid
)
SELECT pg_catalog.encode(pg_catalog.sha256(pg_catalog.convert_to(
         COALESCE(pg_catalog.string_agg(v, E'\n' ORDER BY v COLLATE "C"), ''), 'UTF8')), 'hex') AS digest,
       (SELECT pg_catalog.count(*) FROM t) AS relations
FROM line
