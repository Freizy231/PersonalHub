export const widgetNames = { capture:'快速记录', tasks:'今日任务', events:'今日日程', reminders:'提醒收件箱', shortcuts:'快捷入口', reading:'正在阅读', learning:'学习计划', weekly:'本周成果', projects:'最近项目', meals:'今日三餐', intelligence:'技术情报' } as const
export type WidgetId=keyof typeof widgetNames
export interface Widget {id:WidgetId;enabled:boolean}
export interface Shortcut {id:string;label:string;kind:'internal'|'web';target:string}
export interface Preferences {schema_version:1;widgets:Widget[];shortcuts:Shortcut[];version:number;state:'default'|'saved'|'invalid';updated_at:string|null}
export const internalTargets=[{target:'/',label:'首页'},{target:'/tasks',label:'任务'},{target:'/events',label:'日程'},{target:'/notes',label:'笔记'},{target:'/vault',label:'私密库（需解锁）'},{target:'/diet',label:'饮食'},{target:'/finance',label:'财务'},{target:'/exercise',label:'运动'},{target:'/learning',label:'学习'},{target:'/projects',label:'项目'},{target:'/search',label:'全局搜索'},{target:'/library',label:'资料库'},{target:'/intelligence',label:'情报'},{target:'/ai',label:'AI 助手'},{target:'/settings',label:'设置'}]
export function defaults():Preferences{return {schema_version:1,widgets:(Object.keys(widgetNames) as WidgetId[]).map(id=>({id,enabled:!(['reading','learning','weekly','projects','meals','intelligence'] as string[]).includes(id)})),shortcuts:[
  {id:'00000000-0000-4000-8000-000000000001',label:'今日任务',kind:'internal',target:'/tasks'},
  {id:'00000000-0000-4000-8000-000000000002',label:'笔记',kind:'internal',target:'/notes'},
  {id:'00000000-0000-4000-8000-000000000003',label:'技术情报',kind:'internal',target:'/intelligence'},
],version:0,state:'default',updated_at:null}}
/** Routes saved before 0.14 ("/life?view=notes") still resolve, to the page that replaced them. */
export const legacyTargets:Record<string,string>={'/life':'/tasks','/life?view=tasks':'/tasks','/life?view=events':'/events','/life?view=notes':'/notes','/life?view=vault':'/vault','/life?view=diet':'/diet','/life?view=finance':'/finance','/life?view=exercise':'/exercise'}
export function safeTarget(kind:string,target:string):string|null{
  if(kind==='internal')return internalTargets.some(item=>item.target===target)?target:legacyTargets[target]??null
  if(kind!=='web'||target.length>2048||!/^https?:\/\//i.test(target)||!/^[\x21-\x7e]+$/.test(target)||/[\\?#]/.test(target)||/%(?:0[0-9a-f]|1[0-9a-f]|7f|5c)/i.test(target))return null
  try{const url=new URL(target);if(url.username||url.password||target.split('://')[1]?.split('/')[0]?.includes('@')||!url.hostname||url.port==='0')return null;return url.href}catch{return null}
}
export function usable(value:Preferences):boolean{
  return value.schema_version===1&&Number.isInteger(value.version)&&value.version>=0&&['default','saved','invalid'].includes(value.state)&&Array.isArray(value.widgets)&&value.widgets.length>=5&&value.widgets.length<=11&&new Set(value.widgets.map(w=>w.id)).size===value.widgets.length&&['capture','tasks','events','reminders','shortcuts'].every(id=>value.widgets.some(w=>w.id===id))&&value.widgets.every(w=>Object.hasOwn(widgetNames,w.id)&&typeof w.enabled==='boolean')&&Array.isArray(value.shortcuts)&&value.shortcuts.length<=12&&new Set(value.shortcuts.map(s=>s.id)).size===value.shortcuts.length&&value.shortcuts.every(s=>typeof s.id==='string'&&typeof s.label==='string'&&!!s.label.trim()&&s.label.length<=80&&typeof s.target==='string'&&!!safeTarget(s.kind,s.target))
}
