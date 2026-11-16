<script setup lang="ts">
withDefaults(defineProps<{name:string;size?:number}>(),{size:20})
const paths:Record<string,string[]>={
 home:['M3 10.5 12 3l9 7.5','M5 9v11h5v-6h4v6h5V9'],
 life:['M8 2v4m8-4v4M3 10h18','M5 4h14a2 2 0 0 1 2 2v14H3V6a2 2 0 0 1 2-2Z','m8 15 2 2 5-5'],
 learning:['M12 7c-3-3-7-3-10-2v14c3-1 7-1 10 2 3-3 7-3 10-2V5c-3-1-7-1-10 2Z','M12 7v14'],
 projects:['M9 3H4v18h16V8l-5-5H9Z','M14 3v6h6','m8 13-2 2 2 2m8-4 2 2-2 2m-3-5-2 7'],
 intelligence:['M4 16v5m5-10v10m5-15v15m5-19v19','m3 9 5-4 5 2 7-6'],
 library:['M4 4h4v17H4zM11 4h4v17h-4z','m17 5 3-1 4 16-3 1z'],
 ai:['m12 3 2.4 6.6L21 12l-6.6 2.4L12 21l-2.4-6.6L3 12l6.6-2.4L12 3Z','M20 2v4m-2-2h4'],
 settings:['M12 8a4 4 0 1 0 0 8 4 4 0 0 0 0-8Z','M10 2h4l1 3 3 1 3 3-2 3 2 3-3 3-3 1-1 3h-4l-1-3-3-1-3-3 2-3-2-3 3-3 3-1 1-3Z'],
 search:['M10.5 3a7.5 7.5 0 1 0 0 15 7.5 7.5 0 0 0 0-15Z','m16 16 5 5'],
 plus:['M12 5v14M5 12h14'],arrow:['M4 12h16m-6-6 6 6-6 6'],sun:['M12 8a4 4 0 1 0 0 8 4 4 0 0 0 0-8Z','M12 2v2m0 16v2M2 12h2m16 0h2M5 5l1.5 1.5m11 11L19 19M5 19l1.5-1.5m11-11L19 5'],moon:['M20 15A9 9 0 0 1 9 3a9 9 0 1 0 11 12Z'],
 lock:['M7 11V7a5 5 0 0 1 10 0v4','M5 11h14v10H5z','M12 15v3'],terminal:['m4 6 5 6-5 6m8 0h8'],file:['M5 3h9l5 5v13H5z','M14 3v6h5M8 13h8m-8 4h6'],coffee:['M4 8h12v7a6 6 0 0 1-12 0V8Z','M16 8h2a3 3 0 0 1 0 6h-2M3 22h15M8 2v3m4-3v3'],
check:['M20 6 9 17l-5-5'],calendar:['M3 9h18','M5 4h14a2 2 0 0 1 2 2v14H3V6a2 2 0 0 1 2-2Z','M8 2v4m8-4v4'],
note:['M5 3h9l5 5v13H5z','M14 3v6h5','M8 13h8m-8 4h5'],wallet:['M3 7h16a2 2 0 0 1 2 2v9a2 2 0 0 1-2 2H5a2 2 0 0 1-2-2V7Z','M3 7V5h13','M16 13h2'],
run:['M13 4a2 2 0 1 0 0 4 2 2 0 0 0 0-4Z','m10 9 3 1 1 5 3 3','m10 9-2 6-3 2','m11 15-1 5'],
more:['M5 12h.01M12 12h.01M19 12h.01']
}
</script>
<template><svg :width="size" :height="size" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.6" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true" class="hub-icon"><path v-for="(d,i) in paths[name]||paths.file" :key="i" :d="d"/></svg></template>
