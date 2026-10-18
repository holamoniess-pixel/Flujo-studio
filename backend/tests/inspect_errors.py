from __future__ import annotations

import sqlite3

connection = sqlite3.connect('.flujo/runtime/flujo.sqlite3')
for table in ('error_events', 'audit_events'):
    print(f'--- {table} ---')
    rows = connection.execute(f'SELECT * FROM {table} ORDER BY rowid DESC LIMIT 5').fetchall()
    for row in rows:
        print(row)
