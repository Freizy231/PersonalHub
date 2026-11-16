export interface Tag {id:string;name:string;version:number;usage_count?:number;counts_by_kind?:Record<string,number>}
export interface Assignment {resource_id:string;resource_version:number;tags:Tag[]}
