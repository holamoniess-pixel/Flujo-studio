from __future__ import annotations

import sqlite3

connection = sqlite3.connect('.flujo/runtime/flujo.sqlite3')
row = connection.execute('SELECT timestamp,source,message,stack FROM error_events ORDER BY rowid DESC LIMIT 1').fetchone()
print(row[0:3] if row else 'no errors')
if row and row[3]:
    print(row[3][-1200:])
