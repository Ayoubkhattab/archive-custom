"""
Central access control for Paperless (RBAC + relationship-based access).

Layout (Django equivalent of the requested Node layout):

    permissions.constants.ts  -> paperless/access/constants.py
    policy.engine.ts          -> paperless/access/policy.py   (``can()``)
    permission.service.ts     -> paperless/access/service.py
    requirePermission.ts      -> paperless/access/drf.py      (RequirePermission)
    requireRelationship.ts    -> paperless/access/drf.py      (RequireRelationship)
    audit.service.ts          -> paperless/access/audit.py
    hierarchy (parent/child)  -> paperless/access/hierarchy.py

The parent ("father") of a user is ``UserOwnership.created_by``: the account
that created the user. That relation already existed in this project and is
reused rather than adding a second, competing ``parentId`` column.
"""
