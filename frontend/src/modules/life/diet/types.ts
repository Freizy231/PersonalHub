export const unitNames={g:'克',kg:'千克',ml:'毫升',l:'升',piece:'个',portion:'份'} as const
export type Unit=keyof typeof unitNames
export const slotNames={breakfast:'早餐',lunch:'午餐',dinner:'晚餐'} as const
export type Slot=keyof typeof slotNames
export interface Ingredient {id:string;name:string;version:number}
export interface Line {ingredient_id:string;quantity:string;unit:Unit;name?:string}
export interface Recipe {id:string;title:string;version:number;instructions:string;base_servings:number;favorite:boolean;ingredients:Line[];deleted_at:string|null}
export interface Dish {id:string;recipe_id:string|null;label:string;name:string;recipe_version:number|null;position:number}
export interface Meal {id:string;local_date:string;slot:Slot;recipe_id:string|null;title:string;servings:number;note:string;version:number;dishes:Dish[]}
export interface ShoppingItem extends Line {id:string;name:string;note:string;purchased:boolean;origin:string;removed_at:string|null}
export interface Shopping {version:number;items:ShoppingItem[]}
export interface Stock extends Line {id:string;name:string;version:number}
export interface Suggestion extends Line {name:string;required:string;stock:string;confirmed:string}
export interface Preview {version:number;digest:string;items:Suggestion[];policy:string}
export interface Page<T>{items:T[];total:number;limit:number;offset:number}
export function localDate(){const d=new Date();return `${d.getFullYear()}-${String(d.getMonth()+1).padStart(2,'0')}-${String(d.getDate()).padStart(2,'0')}`}
export function addDays(day:string,delta:number){const d=new Date(day+'T12:00:00Z');d.setUTCDate(d.getUTCDate()+delta);return d.toISOString().slice(0,10)}
export function weekOf(day:string){const d=new Date(day+'T12:00:00Z');return addDays(day,-((d.getUTCDay()+6)%7))}
