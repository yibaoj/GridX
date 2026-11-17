import type {Overview,Page,Province} from './types';

export function overviewRoute(province?:Province):string {
  return province?`overview?province=${encodeURIComponent(province.id)}`:'overview';
}
export function readRoute(hash:string):{page:Page;provinceId:string} {
  const [name,query='']=hash.replace(/^#/,'').split('?');
  const page:Page=['overview','map','simulation','learn'].includes(name)?name as Page:'overview';
  return {page,provinceId:page==='overview'?new URLSearchParams(query).get('province')||'':''};
}
export function provincialStats(data:Overview,province:Province) {
  const ranked=[...data.provinces].sort((a,b)=>b.value-a.value||a.id.localeCompare(b.id));
  return {rank:ranked.findIndex(p=>p.id===province.id)+1,share:data.annual_energy_twh?province.value/data.annual_energy_twh*100:0,ranked};
}
export function overviewExport(data:Overview,province?:Province) {
  const rows=province?[province]:data.provinces;
  return {name:`GridX-${data.year}-${province?province.name:'省级'}用电量.csv`,csv:[['地区','年度用电量 (TWh)','年份'],...rows.map(p=>[p.name,p.value,data.year])].map(r=>r.join(',')).join('\r\n')};
}
