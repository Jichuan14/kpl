import tempfile
from pathlib import Path
import unittest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import Session
from app.database import Base, get_db
from app.models import League
from app.api import leagues, site_default
from app.services.site_settings import resolve_default_league


class SiteDefaultTests(unittest.TestCase):
    def test_uncached_full_catalog_fallback_saved_preference_restart_and_invalid_write(self):
        with tempfile.TemporaryDirectory() as temporary:
            url = f'sqlite:///{Path(temporary) / "settings.db"}'
            engine = create_engine(url, connect_args={'check_same_thread': False})
            Base.metadata.create_all(engine)
            with Session(engine) as db:
                db.add_all([League(league_id='20260003', league_name='S3', year=2026, season=3), League(league_id='20260004', league_name='S4', year=2026, season=4)])
                db.commit()
            app = FastAPI(); app.include_router(leagues.router); app.include_router(site_default.router)
            def temporary_db():
                with Session(engine) as db: yield db
            app.dependency_overrides[get_db] = temporary_db
            with TestClient(app) as client:
                response = client.get('/api/site-default')
                self.assertEqual(response.status_code, 200)
                self.assertEqual(response.headers['cache-control'], 'no-store')
                self.assertEqual(response.json()['data'], {'default_league_id': '20260004', 'saved': False})
                self.assertEqual(client.put('/api/site-default', json={'league_id': '20260003'}).status_code, 405)
                self.assertEqual(client.put('/api/leagues/site-default', json={'league_id': '20260003'}).status_code, 200)
                self.assertEqual(client.put('/api/leagues/site-default', json={'league_id': 'missing'}).status_code, 404)
                self.assertEqual(client.put('/api/leagues/site-default', json={'league_id': '../bad'}).status_code, 422)
                self.assertEqual(client.get('/api/site-default').json()['data']['default_league_id'], '20260003')
            engine.dispose()
            restarted = create_engine(url)
            with Session(restarted) as db:
                self.assertEqual(resolve_default_league(db), {'default_league_id': '20260003', 'saved': True})
            restarted.dispose()

    def test_empty_catalog_is_an_explicit_empty_default(self):
        engine = create_engine('sqlite://'); Base.metadata.create_all(engine)
        with Session(engine) as db:
            self.assertEqual(resolve_default_league(db), {'default_league_id': None, 'saved': False})

    def test_production_nginx_exposes_read_only_default_and_keeps_write_private(self):
        config = (Path(__file__).resolve().parents[2] / 'frontend' / 'nginx.conf').read_text()
        read = config.split('location = /api/site-default {', 1)[1].split('\n    }', 1)[0]
        self.assertIn('limit_except GET { deny all; }', read)
        self.assertIn('no-store', read)
        private = config.split('location /api/leagues {', 1)[1].split('\n    }', 1)[0]
        self.assertIn('auth_basic "KPL management";', private)
        self.assertNotIn('location = /api/leagues/site-default', config)
