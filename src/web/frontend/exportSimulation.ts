import type {Application} from './types';

export function firstDayEnd(hours:number,firstDay:boolean):number {
  if(!firstDay||hours<=24)return 100;
  return 100*23/(hours-1);
}

export const seriesTitle=(series:Application['series'][number],label=series.id)=>`${label}${series.kind==='storage_charge'?' · 充电':series.kind==='storage_discharge'?' · 放电':''}`;

/** Match ECharts category-axis percentages, including both endpoint samples. */
export function simulationCSV(data:Application,start=0,end=100,selected:Record<string,boolean>={},names:Record<string,string>={}){
  // Percentages can round just below an integer sample at the selected endpoint.
  const epsilon=1e-9;
  const first=Math.max(0,Math.ceil(start/100*(data.time.length-1)-epsilon));
  const last=Math.min(data.time.length-1,Math.floor(end/100*(data.time.length-1)+epsilon));
  const series=data.series.filter(s=>selected[seriesTitle(s,names[s.id]||s.id)]!==false);
  const rows=[['time (source local time)',...series.map(s=>`${s.kind}:${s.id} (MW)`)],...data.time.slice(first,last+1).map((t,i)=>[t,...series.map(s=>s.values[i+first])])];
  return {content:rows.map(row=>row.join(',')).join('\r\n'),hours:Math.max(0,last-first+1),series:series.length};
}
