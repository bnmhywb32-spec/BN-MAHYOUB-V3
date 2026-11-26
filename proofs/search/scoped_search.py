"""Authorized search fixture sharing one SQLite transaction with the session store."""


def find_authorized(hub, search, token, branch, query, limit=20):
    if hub.db is not search.db:
        raise ValueError("SEARCH_DATABASE_MISMATCH")
    if not isinstance(branch, str) or not branch:
        raise ValueError("INVALID_SCOPE")
    db = hub.db
    db.execute("SAVEPOINT authorized_search")
    try:
        company, device = hub._scope(token)
        allowed = db.execute(
            "SELECT 1 FROM search_scopes WHERE company=? AND device=? AND branch=?",
            (company, device, branch),
        ).fetchone()
        if allowed is None:
            raise ValueError("SEARCH_DENIED")
        return search.find(company, branch, query, limit)
    except BaseException:
        db.execute("ROLLBACK TO authorized_search")
        raise
    finally:
        db.execute("RELEASE authorized_search")
