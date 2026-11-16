"""EPUB import, extraction and hostile-archive handling.

Every fixture is generated locally. No real book, no network, no DRM content.
"""
import hashlib
import httpx
import os
import tempfile
import unittest
import zipfile
from io import BytesIO
from pathlib import Path
from uuid import uuid4

from backend.app.core.config import Settings
from backend.app.core.database import HubError, connection
from backend.app.main import create_app
from backend.app.modules.knowledge.search import SearchService
from backend.app.modules.library.documents.epub import extract
from backend.app.modules.library.documents.service import DocumentService, detect_format
from backend.app.modules.library.documents.worker import ExtractionWorker
from backend.manage import initialize_empty
from backend.maintenance import upgrade
from backend.tests.epub_fixtures import epub
from backend.tests.async_support import AsyncAPITestCase


def key(): return str(uuid4())


class EpubParserTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name)

    def tearDown(self):
        self.tmp.cleanup()

    def parse(self, data):
        path = self.root / 'book.epub'
        path.write_bytes(data)
        result = extract(path)
        # The extractor returns UTF-8 bytes; tests compare decoded text.
        result['text'] = result['text'].decode('utf-8')
        return result

    def test_reads_spine_in_order_and_metadata(self):
        result = self.parse(epub(text='第一章内容', chapters=3, title='测试书名', author='某作者'))
        self.assertEqual(result['page_count'], 3)
        self.assertEqual(result['chapter_count'], 3)
        self.assertEqual(result['title'], '测试书名')
        self.assertEqual(result['author'], '某作者')
        self.assertIn('第一章内容', result['text'])
        # Chapters must stay in spine order.
        self.assertLess(result['text'].index('1'), result['text'].index('2'))
        self.assertLess(result['text'].index('2'), result['text'].index('3'))

    def test_empty_book_extracts_no_text(self):
        self.assertEqual(self.parse(epub(empty=True))['text'], '')

    def test_rejects_non_zip(self):
        with self.assertRaises(HubError) as error:
            self.parse(b'not an epub')
        self.assertEqual(error.exception.code, 'EPUB_INVALID')

    def test_rejects_path_traversal(self):
        with self.assertRaises(HubError) as error:
            self.parse(epub(traversal=True))
        self.assertEqual(error.exception.code, 'EPUB_UNSAFE')

    def test_rejects_doctype_and_entity_declarations(self):
        with self.assertRaises(HubError) as error:
            self.parse(epub(doctype=True))
        self.assertEqual(error.exception.code, 'EPUB_XML_UNSAFE')

    def test_rejects_zip_bomb_ratio_and_entry_size(self):
        with self.assertRaises(HubError) as error:
            self.parse(epub(bomb=True))
        self.assertEqual(error.exception.code, 'EPUB_RESOURCE_BUDGET')

    def test_rejects_encrypted_entries(self):
        with self.assertRaises(HubError) as error:
            self.parse(epub(encrypted=True))
        self.assertEqual(error.exception.code, 'EPUB_ENCRYPTED')

    def test_strips_scripts_and_styles_from_text(self):
        buffer = BytesIO()
        with zipfile.ZipFile(buffer, 'w') as archive:
            archive.writestr('META-INF/container.xml',
                             '<container><rootfiles><rootfile full-path="c.opf"/></rootfiles></container>')
            archive.writestr('c.opf', '<package><metadata><dc:title>T</dc:title></metadata>'
                                     '<manifest><item id="x" href="a.xhtml" media-type="application/xhtml+xml"/></manifest>'
                                     '<spine><itemref idref="x"/></spine></package>')
            archive.writestr('a.xhtml', '<html><head><title>T</title></head><body>'
                                        '<script>alert(1)</script><style>p{}</style>'
                                        '<p>正文 &amp; 符号</p></body></html>')
        result = self.parse(buffer.getvalue())
        self.assertIn('正文 & 符号', result['text'])
        self.assertNotIn('alert', result['text'])
        self.assertNotIn('p{}', result['text'])


class EpubHardeningTests(EpubParserTests):
    """Hostile or sloppy files must fail fast and controlled, never hang or crash the worker."""

    def test_markup_that_made_regexes_quadratic_is_linear(self):
        import time
        from backend.app.modules.library.documents.epub import _text
        for hostile in ('<script id=1>' * 60000, '<a ' * 250000, '<!--' * 200000, '&' * 500000, '<' * 500000):
            started = time.perf_counter()
            _text('<p>keep</p>' + hostile)
            self.assertLess(time.perf_counter() - started, 2.0, 'extraction must stay linear on hostile markup')

    def test_visible_text_survives_the_linear_scanner(self):
        from backend.app.modules.library.documents.epub import _text
        html = ('<html><head><title>T</title><style>p{}</style></head><body><h1>Title</h1>'
                '<p>One &amp; two<br/>three</p><ul><li>a</li><li>b</li></ul><script>x()</script>'
                '<!-- note --><p>end&nbsp;here &#65; &#x42;</p></body></html>')
        self.assertEqual(_text(html), 'Title\nOne & two\nthree\na\nb\nend here A B')

    def test_lone_surrogate_entities_do_not_abort_the_import(self):
        from backend.app.modules.library.documents.epub import _text
        value = _text('<p>a &#xD800; b &#55357; c</p>')
        value.encode('utf-8')
        self.assertEqual(value, 'a b c')

    def test_percent_encoded_hrefs_and_fragments_resolve(self):
        buffer = BytesIO()
        with zipfile.ZipFile(buffer, 'w') as archive:
            archive.writestr('META-INF/container.xml', '<container><rootfiles><rootfile full-path="c.opf"/></rootfiles></container>')
            archive.writestr('c.opf', '<package><manifest><item id="a" href="my%20chapter.xhtml#top" media-type="application/xhtml+xml"/>'
                                      '<item id="b" href="%E7%AC%AC%E4%BA%8C.xhtml" media-type="application/xhtml+xml"/></manifest>'
                                      '<spine><itemref idref="a"/><itemref idref="b"/></spine></package>')
            archive.writestr('my chapter.xhtml', '<p>hello</p>')
            archive.writestr('第二.xhtml', '<p>world</p>')
        result = self.parse(buffer.getvalue())
        self.assertEqual(result['text'], 'hello\n\nworld')

    def test_corrupt_archive_members_are_a_controlled_refusal(self):
        data = bytearray(epub())
        for position in range(60, 120):
            data[position] ^= 0xFF
        with self.assertRaises(HubError) as raised:
            self.parse(bytes(data))
        self.assertIn(raised.exception.code, ('EPUB_INVALID', 'EPUB_UNSAFE', 'EPUB_RESOURCE_BUDGET'))

    def test_total_bytes_across_the_spine_are_bounded(self):
        from backend.app.modules.library.documents import epub as module
        buffer = BytesIO()
        with zipfile.ZipFile(buffer, 'w', zipfile.ZIP_DEFLATED) as archive:
            archive.writestr('META-INF/container.xml', '<container><rootfiles><rootfile full-path="c.opf"/></rootfiles></container>')
            archive.writestr('c.opf', '<package><manifest><item id="a" href="a.xhtml" media-type="application/xhtml+xml"/></manifest><spine>'
                                      + '<itemref idref="a"/>' * 40 + '</spine></package>')
            archive.writestr('a.xhtml', '<p>' + 'x' * 1_000_000 + '</p>')
        original = module.MAX_SPINE_BYTES
        module.MAX_SPINE_BYTES = 8 * 1024 * 1024
        try:
            with self.assertRaises(HubError) as raised:
                self.parse(buffer.getvalue())
            self.assertEqual(raised.exception.code, 'EPUB_RESOURCE_BUDGET')
        finally:
            module.MAX_SPINE_BYTES = original


class EpubImportTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name)
        self.settings = Settings(data_root=self.root / 'hub', legacy_database=self.root / 'legacy',
                                 reminders_enabled=False)
        initialize_empty(self.settings.data_root)
        upgrade(self.settings)
        self.d = DocumentService(self.settings.database)
        self.worker = ExtractionWorker(self.settings)

    def tearDown(self):
        self.tmp.cleanup()

    def add(self, data, name='book.epub'):
        iid = key()
        relative, fd = self.d.begin(iid, 'epub')
        with os.fdopen(fd, 'wb') as handle:
            handle.write(data)
            handle.flush()
            os.fsync(handle.fileno())
        return self.d.commit(iid, relative, len(data), hashlib.sha256(data).hexdigest(), name, key(),
                             detect_format(data[:8], name))

    def test_detects_epub_by_magic_not_by_filename(self):
        self.assertEqual(detect_format(b'PK\x03\x04rest', 'book.epub'), 'epub')
        self.assertEqual(detect_format(b'%PDF-1.7', 'book.pdf'), 'pdf')
        self.assertIsNone(detect_format(b'GIF89a', 'book.pdf'))
        # A zip named .pdf is still a zip; the magic decides.
        self.assertEqual(detect_format(b'PK\x03\x04rest', 'book.pdf'), 'epub')

    def test_import_stores_epub_blob_and_exposes_format(self):
        document = self.add(epub()).get('document')
        self.assertEqual(document['format'], 'epub')
        self.assertEqual(document['size'], len(epub()))
        data, fmt = self.d.original(document['id'])
        self.assertEqual(fmt, 'epub')
        self.assertEqual(data, epub())

    def test_worker_extracts_epub_text_metadata_and_page_count(self):
        document = self.add(epub(text='可检索正文', chapters=2, title='书名', author='作者')).get('document')
        self.assertTrue(self.worker.once())
        stored = self.d.get(document['id'])
        self.assertEqual(stored['extraction_status'], 'ready')
        self.assertEqual(stored['page_count'], 2)
        self.assertEqual(stored['metadata_title'], '书名')
        self.assertEqual(stored['metadata_author'], '作者')
        self.assertEqual(stored['extractor'], 'epub-builtin-v1')
        with connection(self.settings.database) as con:
            path, digest = con.exec_driver_sql(
                'SELECT extracted_text_path,text_sha256 FROM documents WHERE resource_id=?',
                (document['id'],)).one()
        self.assertIn('可检索正文', self.d.files.read(path, digest).decode('utf-8'))

    def test_epub_text_is_searchable_and_in_portable_export(self):
        document = self.add(epub(text='独特标记词')).get('document')
        self.worker.once()
        SearchService(self.settings.database).rebuild()
        self.assertEqual(SearchService(self.settings.database).query('独特标记词')['total'], 1)
        from backend.app.modules.knowledge.export import portable_export
        archive = zipfile.ZipFile(BytesIO(portable_export(self.settings.database)))
        self.assertTrue(any(name.endswith('.epub') for name in archive.namelist()))

    def test_empty_epub_becomes_no_text_not_failure(self):
        document = self.add(epub(empty=True)).get('document')
        self.worker.once()
        self.assertEqual(self.d.get(document['id'])['extraction_status'], 'no_text')

    def test_malformed_epub_passes_import_but_fails_extraction_without_text(self):
        bogus = b'PK\x03\x04broken-archive'
        document = self.add(bogus).get('document')
        self.worker.once()
        stored = self.d.get(document['id'])
        self.assertEqual(stored['extraction_status'], 'failed')
        with connection(self.settings.database) as con:
            self.assertIsNone(con.exec_driver_sql(
                'SELECT extracted_text_path FROM documents WHERE resource_id=?', (document['id'],)).scalar_one())

    def test_unexpected_parser_failure_ends_the_job_instead_of_leaving_it_running(self):
        from unittest import mock
        document = self.add(epub()).get('document')
        with mock.patch('backend.app.modules.library.documents.worker.extract_epub', side_effect=ValueError('unexpected')):
            self.assertTrue(self.worker.once())
        stored = self.d.get(document['id'])
        self.assertEqual(stored['extraction_status'], 'failed')
        with connection(self.settings.database) as con:
            state, code = con.exec_driver_sql('SELECT state,error_code FROM extraction_jobs WHERE document_id=?', (document['id'],)).one()
        self.assertEqual((state, code), ('failed', 'EXTRACTION_FAILED'))

    def test_a_job_that_keeps_dying_is_failed_instead_of_requeued_forever(self):
        document = self.add(epub()).get('document')
        with connection(self.settings.database, write=True) as con:
            con.exec_driver_sql("UPDATE extraction_jobs SET state='running',attempts=3 WHERE document_id=?", (document['id'],))
            con.exec_driver_sql("UPDATE documents SET extraction_status='running' WHERE resource_id=?", (document['id'],))
        self.worker.recover()
        with connection(self.settings.database) as con:
            state, code = con.exec_driver_sql('SELECT state,error_code FROM extraction_jobs WHERE document_id=?', (document['id'],)).one()
        self.assertEqual((state, code), ('failed', 'EXTRACTION_REPEATED_RESTART'))
        self.assertEqual(self.d.get(document['id'])['extraction_status'], 'failed')

    def test_a_job_interrupted_once_is_still_retried_after_restart(self):
        document = self.add(epub()).get('document')
        with connection(self.settings.database, write=True) as con:
            con.exec_driver_sql("UPDATE extraction_jobs SET state='running',attempts=1 WHERE document_id=?", (document['id'],))
        self.worker.recover()
        with connection(self.settings.database) as con:
            state = con.exec_driver_sql('SELECT state FROM extraction_jobs WHERE document_id=?', (document['id'],)).scalar_one()
        self.assertEqual(state, 'queued')

    def test_encrypted_epub_is_reported_as_password_required(self):
        document = self.add(epub(encrypted=True)).get('document')
        self.worker.once()
        self.assertEqual(self.d.get(document['id'])['extraction_status'], 'password_required')


class EpubApiTests(AsyncAPITestCase):
    async def asyncSetUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name)
        self.settings = Settings(data_root=self.root / 'hub', legacy_database=self.root / 'legacy',
                                 reminders_enabled=False)
        initialize_empty(self.settings.data_root)
        upgrade(self.settings)
        self.app = create_app(self.settings)
        self.client = httpx.AsyncClient(transport=httpx.ASGITransport(app=self.app),
                                        base_url='http://127.0.0.1:8765')
        response = await self.client.post('/api/v1/session/exchange',
                                          json={'token': self.app.state.auth.bootstrap},
                                          headers={'Origin': 'http://127.0.0.1:8765'})
        self.headers = {'Origin': 'http://127.0.0.1:8765',
                        'X-CSRF-Token': response.json()['csrf_token'],
                        'Idempotency-Key': key()}

    async def asyncTearDown(self):
        await self.client.aclose()
        self.tmp.cleanup()

    async def test_upload_epub_and_download_with_epub_media_type(self):
        response = await self.client.post('/api/v1/documents/import?name=book.epub', content=epub(),
                                          headers=self.headers)
        self.assertEqual(response.status_code, 200, response.text)
        document = response.json()['document']
        self.assertEqual(document['format'], 'epub')
        original = await self.client.get(f"/api/v1/documents/{document['id']}/original")
        self.assertEqual(original.status_code, 200)
        self.assertEqual(original.headers['content-type'], 'application/epub+zip')
        self.assertEqual(original.content, epub())

    async def test_pdf_upload_still_works_after_epub_support(self):
        from backend.tests.pdf_fixtures import pdf
        response = await self.client.post('/api/v1/documents/import?name=paper.pdf', content=pdf(),
                                          headers=self.headers)
        self.assertEqual(response.status_code, 200, response.text)
        self.assertEqual(response.json()['document']['format'], 'pdf')

    async def test_unknown_binary_is_rejected(self):
        response = await self.client.post('/api/v1/documents/import?name=x.epub', content=b'GIF89a-not-a-book',
                                          headers={**self.headers, 'Idempotency-Key': key()})
        self.assertEqual(response.status_code, 422)
        self.assertEqual(response.json()['code'], 'PDF_SIGNATURE_INVALID')


if __name__ == '__main__':
    unittest.main()
