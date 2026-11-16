/** A deliberately small, text-only Markdown presentation. No raw HTML or images. */
export interface Block {kind:'heading'|'paragraph'|'code'|'quote'|'ul'|'ol'|'rule';text?:string;level?:number;items?:string[]}
export interface Inline {kind:'text'|'strong'|'code'|'em'|'link';text:string;href?:string}
export function safeLink(value:string):string|null{
  if(/^hub:\/\/resource\/[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$/i.test(value))return '/resource/'+value.slice(15)
  try{const u=new URL(value);if(!['https:','http:'].includes(u.protocol)||u.username||u.password)return null;return u.href}catch{return null}
}
export function inline(text:string):Inline[]{
  const parts:Inline[]=[];const pattern=/(`[^`\n]+`|\*\*[^*\n]+\*\*|\*[^*\n]+\*|(?<!!)\[[^\]\n]+\]\([^\s)]+\))/g;let at=0
  for(const m of text.matchAll(pattern)){const i=m.index!;if(i>at)parts.push({kind:'text',text:text.slice(at,i)});const s=m[0]
    if(s.startsWith('`'))parts.push({kind:'code',text:s.slice(1,-1)})
    else if(s.startsWith('**'))parts.push({kind:'strong',text:s.slice(2,-2)})
    else if(s.startsWith('*'))parts.push({kind:'em',text:s.slice(1,-1)})
    else{const match=/^\[([^\]]+)\]\(([^)]+)\)$/.exec(s)!;const href=safeLink(match[2]!);parts.push(href?{kind:'link',text:match[1]!,href}:{kind:'text',text:s})}at=i+s.length
  }if(at<text.length)parts.push({kind:'text',text:text.slice(at)});return parts
}
export function blocks(body:string):Block[]{
  const lines=body.replace(/\r\n?/g,'\n').split('\n'),result:Block[]=[];let paragraph:string[]=[]
  const flush=()=>{if(paragraph.length){result.push({kind:'paragraph',text:paragraph.join('\n')});paragraph=[]}}
  for(let i=0;i<lines.length;i++){const line=lines[i]!,heading=/^(#{1,6})\s+(.+)$/.exec(line),list=/^\s*([-+*]|\d+[.)])\s+(.+)$/.exec(line)
    if(/^\s*```/.test(line)){flush();const code:string[]=[];while(++i<lines.length&&!/^\s*```\s*$/.test(lines[i]!))code.push(lines[i]!);result.push({kind:'code',text:code.join('\n')})}
    else if(heading){flush();result.push({kind:'heading',level:Math.min(6,heading[1]!.length+1),text:heading[2]!})}
    else if(/^\s*(?:---+|\*\*\*+)\s*$/.test(line)){flush();result.push({kind:'rule'})}
    else if(/^>\s?/.test(line)){flush();const quote=[line.replace(/^>\s?/,'')];while(i+1<lines.length&&/^>\s?/.test(lines[i+1]!))quote.push(lines[++i]!.replace(/^>\s?/,''));result.push({kind:'quote',text:quote.join('\n')})}
    else if(list){flush();const kind=/^\d/.test(list[1]!)?'ol':'ul';const previous=result.at(-1);if(previous?.kind===kind)previous.items!.push(list[2]!);else result.push({kind,items:[list[2]!]})}
    else if(!line.trim())flush();else paragraph.push(line)
  }flush();return result
}
