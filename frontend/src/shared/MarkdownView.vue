<script lang="ts">
import {computed,defineComponent,h} from 'vue'
import {blocks,inline} from './markdown'
export default defineComponent({props:{body:{type:String,required:true}},setup(props){
 const content=computed(()=>blocks(props.body))
 const text=(s:string)=>inline(s).map((p,i)=>p.kind==='link'?h('a',{key:i,href:p.href,target:p.href!.startsWith('/')?undefined:'_blank',rel:'noopener noreferrer'},p.text):p.kind==='text'?p.text:h(p.kind,{key:i},p.text))
 return()=>h('div',{class:'reading-prose'},content.value.map((b,i)=>b.kind==='code'?h('pre',{key:i},[h('code',b.text)]):b.kind==='rule'?h('hr',{key:i}):b.kind==='ul'||b.kind==='ol'?h(b.kind,{key:i},b.items!.map((v,j)=>h('li',{key:j},text(v)))):h(b.kind==='heading'?'h'+b.level:b.kind==='quote'?'blockquote':'p',{key:i},text(b.text||''))))
}})
</script>
