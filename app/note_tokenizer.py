"""Exact SQLite FTS5 unicode61 lexical compatibility, without file authority.

Each call opens only a disposable :memory: database. It stores no persistent
business data or search index. PostgreSQL owns Notes and derived token positions.
FTS5 instance offsets preserve adjacency and field boundaries, including CJK,
Latin diacritics/case and underscore token splits. This requires SQLite FTS5.
"""
from contextlib import closing
import re
import sqlite3


def tokenize_fields(body: str, concept: str = '', tags: str = '') -> tuple[tuple[str, int, str], ...]:
    with closing(sqlite3.connect(':memory:')) as conn:
        conn.execute('create virtual table lex using fts5(body_markdown,concept,tags_text)')
        conn.execute('create virtual table vocab using fts5vocab(lex, instance)')
        conn.execute('insert into lex values (?,?,?)', (body, concept, tags))
        return tuple(conn.execute('select col,offset,term from vocab order by col,offset'))


def query_groups(match_query: str) -> tuple[tuple[str, ...], ...]:
    """Parse only the quoted phrase-prefix form emitted by _fts_query.

    Groups are ANDed. Only their last token is a prefix; an empty lexical group
    cannot match, just as an empty FTS5 phrase cannot match.
    """
    return tuple(tuple(token for _, _, token in tokenize_fields(group))
                 for group in re.findall(r'"([^"]*)"\*', match_query))
