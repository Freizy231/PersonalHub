"""Locally generated EPUB archives for tests. No downloads, no real book text."""
import io
import zipfile


def epub(text='Personal Hub EPUB chapter', chapters=1, title='测试书籍', author='测试作者',
         encrypted=False, doctype=False, bomb=False, traversal=False, empty=False):
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, 'w', compression=zipfile.ZIP_DEFLATED) as archive:
        archive.writestr('mimetype', 'application/epub+zip')
        archive.writestr('META-INF/container.xml', '''<?xml version="1.0"?>
<container version="1.0" xmlns="urn:oasis:names:tc:opendocument:xmlns:container">
  <rootfiles><rootfile full-path="OEBPS/content.opf" media-type="application/oebps-package+xml"/></rootfiles>
</container>''')
        items = []
        spine = []
        for index in range(chapters):
            name = f'chapter{index + 1}.xhtml'
            body = '' if empty else f'<p>{text} {index + 1}</p>'
            archive.writestr('OEBPS/' + name, f'''<?xml version="1.0" encoding="utf-8"?>
<!DOCTYPE html>''' if doctype else '''<?xml version="1.0" encoding="utf-8"?>
<html xmlns="http://www.w3.org/1999/xhtml"><head><title>c</title></head>
<body>''' + body + '</body></html>')
            items.append(f'<item id="c{index + 1}" href="{name}" media-type="application/xhtml+xml"/>')
            spine.append(f'<itemref idref="c{index + 1}"/>')
        if traversal:
            items.append('<item id="evil" href="../../etc/passwd" media-type="application/xhtml+xml"/>')
            spine.append('<itemref idref="evil"/>')
        archive.writestr('OEBPS/content.opf', f'''<?xml version="1.0" encoding="utf-8"?>
<package xmlns="http://www.idpf.org/2007/opf" version="3.0" unique-identifier="id">
  <metadata xmlns:dc="http://purl.org/dc/elements/1.1/">
    <dc:title>{title}</dc:title><dc:creator>{author}</dc:creator>
    <dc:language>zh-CN</dc:language><dc:date>2026-11-13</dc:date>
  </metadata>
  <manifest>{''.join(items)}</manifest>
  <spine>{''.join(spine)}</spine>
</package>''')
        if bomb:
            # Highly compressible payload far beyond the per-entry limit.
            archive.writestr('OEBPS/bomb.xhtml', b'<' + b'a' * 9_000_000)
    data = buffer.getvalue()
    if encrypted:
        # Flip the general-purpose "encrypted" bit on the central directory entries.
        data = bytearray(data)
        for index in range(len(data) - 4):
            if data[index:index + 4] == b'PK\x01\x02':
                flags = int.from_bytes(data[index + 8:index + 10], 'little') | 1
                data[index + 8:index + 10] = flags.to_bytes(2, 'little')
        data = bytes(data)
    return data
