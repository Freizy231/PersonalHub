import { ref } from 'vue'

/**
 * Every dialog teleports into <body> at the same base z-index, so a confirmation
 * raised from inside a form ties with the form. With equal z-index the later
 * element in the document wins, and that is not the dialog the person just
 * opened: a confirmation could render completely behind the form that raised it,
 * leaving an "un-closable" panel whose only visible control still clicks through
 * to the form underneath.
 *
 * So dialogs are stacked explicitly in open order: the newest is always on top.
 * Depth is one step, which leaves the base band well clear of the command palette
 * (70) and the toast stack (80) that must stay above every dialog.
 */
export const dialogStack = ref<string[]>([])

let seq = 0
let locks = 0
let priorOverflow = ''

export function enterDialog() {
  const id = `dialog-${++seq}`
  dialogStack.value = [...dialogStack.value, id]
  // Nested dialogs each want the page frozen; only the first and last matter.
  if (locks++ === 0) {
    priorOverflow = document.body.style.overflow || ''
    document.body.style.overflow = 'hidden'
  }
  return {
    depth: () => Math.max(0, dialogStack.value.indexOf(id)),
    leave() {
      if (!dialogStack.value.includes(id)) return
      dialogStack.value = dialogStack.value.filter(item => item !== id)
      if (--locks === 0) document.body.style.overflow = priorOverflow
    },
  }
}
