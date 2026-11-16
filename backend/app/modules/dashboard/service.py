import json
from pydantic import ValidationError
from sqlalchemy import text
from ...core.database import HubError, connection, utc_now
from ...core.operations import replay, record
from .schemas import Preferences, default_preferences, WIDGET_IDS


class DashboardService:
    def __init__(self, database): self.database = database

    def _read(self, con):
        row = con.execute(text("SELECT * FROM dashboard_preferences WHERE id='dashboard'")).mappings().first()
        if row is None: return {**default_preferences(), 'version': 0, 'state': 'default', 'updated_at': None}
        try:
            if len(row['widgets_json'])+len(row['shortcuts_json']) > 65536: raise ValueError
            if type(row['schema_version']) is not int or row['schema_version'] != 1: raise ValueError
            prefs = Preferences(schema_version=row['schema_version'], widgets=json.loads(row['widgets_json']), shortcuts=json.loads(row['shortcuts_json']))
            values=prefs.model_dump(mode='json')
            values['widgets'] += [{'id':name,'enabled':False} for name in WIDGET_IDS if name not in {w['id'] for w in values['widgets']}]
            return {**values, 'version': row['version'], 'state': 'saved', 'updated_at': row['updated_at']}
        except (ValueError, TypeError, ValidationError):
            return {**default_preferences(), 'shortcuts': [], 'version': row['version'], 'state': 'invalid', 'updated_at': row['updated_at']}

    def get(self):
        with connection(self.database) as con: return self._read(con)

    def save(self, payload, key):
        values = payload.model_dump(mode='json')
        with connection(self.database, write=True) as con:
            previous = replay(con, 'dashboard.preferences', key, values)
            current = self._read(con)
            if current['state'] == 'invalid': raise HubError('DASHBOARD_CONFIG_INVALID', 409)
            if previous is not None: return current
            if payload.expected_version != current['version']: raise HubError('VERSION_CONFLICT', 409)
            instant = utc_now(); version = current['version'] + 1
            params = {'schema': payload.schema_version, 'widgets': json.dumps(values['widgets'], ensure_ascii=False),
                      'shortcuts': json.dumps(values['shortcuts'], ensure_ascii=False), 'version': version, 'updated': instant}
            if current['version'] == 0:
                con.execute(text('''INSERT INTO dashboard_preferences
                    (id,schema_version,widgets_json,shortcuts_json,version,created_at,updated_at)
                    VALUES ('dashboard',:schema,:widgets,:shortcuts,:version,:updated,:updated)'''), params)
            else:
                con.execute(text('''UPDATE dashboard_preferences SET schema_version=:schema,widgets_json=:widgets,
                    shortcuts_json=:shortcuts,version=:version,updated_at=:updated WHERE id='dashboard' ''') , params)
            record(con, 'dashboard.preferences', key, values, {'applied_version': version})
            return self._read(con)
