"""Small deterministic PDFs created locally, without downloads or personal content."""
def pdf(text='Personal Hub PE import table',blank=False,encrypted=False,chinese=False):
 content=b'q 1 0 0 1 0 0 cm Q' if blank else ('BT /F1 12 Tf 50 700 Td ('+text.replace('(','\\(').replace(')','\\)')+') Tj ET').encode('ascii')
 font=b'<< /Type /Font /Subtype /Type1 /BaseFont /Helvetica >>'
 objects=[b'<< /Type /Catalog /Pages 2 0 R >>',b'<< /Type /Pages /Kids [3 0 R] /Count 1 >>',b'<< /Type /Page /Parent 2 0 R /MediaBox [0 0 612 792] /Resources << /Font << /F1 4 0 R >> >> /Contents 5 0 R >>',font,b'<< /Length '+str(len(content)).encode()+b' >>\nstream\n'+content+b'\nendstream']
 if chinese:
  content=b'BT /F1 12 Tf 50 700 Td <01020304> Tj ET'
  objects[3]=b'<< /Type /Font /Subtype /Type0 /BaseFont /STSong-Light /Encoding /Identity-H /DescendantFonts [6 0 R] /ToUnicode 7 0 R >>'
  objects[4]=b'<< /Length '+str(len(content)).encode()+b' >>\nstream\n'+content+b'\nendstream'
  objects.append(b'<< /Type /Font /Subtype /CIDFontType0 /BaseFont /STSong-Light /CIDSystemInfo << /Registry (Adobe) /Ordering (GB1) /Supplement 0 >> >>')
  cmap=b'/CIDInit /ProcSet findresource begin 12 dict begin begincmap /CIDSystemInfo << /Registry (Adobe) /Ordering (UCS) /Supplement 0 >> def /CMapName /HubUnicode def /CMapType 2 def 1 begincodespacerange <0000> <FFFF> endcodespacerange 4 beginbfchar <0001> <6076> <0002> <610F> <0003> <4EE3> <0004> <7801> endbfchar endcmap CMapName currentdict /CMap defineresource pop end end'
  # CID content must be 16-bit character codes.
  content=b'BT /F1 12 Tf 50 700 Td <0001000200030004> Tj ET'
  objects[4]=b'<< /Length '+str(len(content)).encode()+b' >>\nstream\n'+content+b'\nendstream'
  objects.append(b'<< /Length '+str(len(cmap)).encode()+b' >>\nstream\n'+cmap+b'\nendstream')
 if encrypted:objects.append(b'<< /Filter /Standard /V 1 /R 2 /Length 40 /O <'+b'00'*32+b'> /U <'+b'00'*32+b'> /P -4 >>')
 output=b'%PDF-1.4\n';offsets=[0]
 for i,obj in enumerate(objects,1):offsets.append(len(output));output+=str(i).encode()+b' 0 obj\n'+obj+b'\nendobj\n'
 xref=len(output);output+=b'xref\n0 '+str(len(objects)+1).encode()+b'\n0000000000 65535 f \n'
 for off in offsets[1:]:output+=f'{off:010} 00000 n \n'.encode()
 enc=(b' /Encrypt '+str(len(objects)).encode()+b' 0 R /ID [<'+b'00'*16+b'><'+b'00'*16+b'>]') if encrypted else b''
 return output+b'trailer\n<< /Size '+str(len(objects)+1).encode()+b' /Root 1 0 R'+enc+b' >>\nstartxref\n'+str(xref).encode()+b'\n%%EOF\n'
