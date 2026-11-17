import {useEffect,useState} from 'react';
import type {GeoFeature,Prepared,LayerId,MapSettings} from './types';
export const layerNames:Record<LayerId,string>={generator:'发电',storage:'储能',network:'电网',load:'用电量',resource:'可再生资源',population:'人口'};
export const stages={standard:'标准层',mapping:'映射层',case:'算例层'};
export const stageDescriptions={standard:'规范化的原始数据',mapping:'统一空间网格后的数据',case:'用于系统计算的网络与设备'};
export const labels:Record<string,string>={imports:'区域受电',bioenergy:'生物质',coal:'煤电',gas:'天然气',geothermal:'地热',hydropower:'水电',load_shedding:'未供电负荷',nuclear:'核电',other:'其他',solar:'光伏',wind:'风电',pumped_storage:'抽水蓄能',battery_storage:'电化学储能',compressed_air_storage:'压缩空气',thermal_storage:'储热',capacitor_storage:'电容储能',load:'负荷',onshore:'陆上风电',offshore_fixed:'海上风电',utility_scale_pv:'光伏',run_of_river:'径流水电'};
export const format=(value:number|undefined,digits=0)=>value==null||!Number.isFinite(value)?'—':new Intl.NumberFormat('zh-CN',{maximumFractionDigits:digits}).format(value);
export const dateLabel=(value:string)=>value.replace('T',' ').slice(0,16);
export const duration=()=>matchMedia('(prefers-reduced-motion: reduce)').matches?0:220;
const cache=new Map<string,Promise<unknown>>();
export function request<T>(url:string):Promise<T>{
  if(!cache.has(url)) cache.set(url,fetch(url).then(async r=>{if(!r.ok)throw new Error(`数据请求失败（${r.status}）`);return r.json();}).catch(e=>{cache.delete(url);throw e;}));
  return cache.get(url) as Promise<T>;
}
export function useResource<T>(url:string){const[data,setData]=useState<T>();const[error,setError]=useState('');const[version,setVersion]=useState(0);useEffect(()=>{let active=true;setError('');request<T>(url).then(x=>{if(active)setData(x);}).catch(e=>{if(active)setError(e.message);});return()=>{active=false;};},[url,version]);return{data,error,retry:()=>{cache.delete(url);setVersion(x=>x+1);}};}
export function useLayer(settings:Pick<MapSettings,'stage'|'layer'|'resource'>){
  const[revision,setRevision]=useState(0);
  const key=[settings.stage,settings.layer,settings.resource,revision].join('/');
  const[response,setResponse]=useState<{key:string;data?:Prepared;error?:string}>();
  const current=response?.key===key?response:undefined;
  useEffect(()=>{
    let active=true;
    const worker=new Worker(new URL('./map.worker.ts',import.meta.url),{type:'module'});
    worker.onmessage=({data:message})=>{
      if(active)setResponse(message.error?{key,error:message.error}:{key,data:message});
    };
    worker.onerror=()=>{if(active)setResponse({key,error:'地图数据处理失败，请重试。'});};
    worker.postMessage(settings);
    return()=>{active=false;worker.terminate();};
  },[key]);
  return{data:current?.data,error:current?.error||'',loading:!current,retry:()=>setRevision(x=>x+1)};
}
export function download(name:string,content:Blob|string,type='text/csv;charset=utf-8'){const url=URL.createObjectURL(typeof content==='string'?new Blob(['\ufeff',content],{type}):content);const a=document.createElement('a');a.href=url;a.download=name;document.body.append(a);a.click();a.remove();setTimeout(()=>URL.revokeObjectURL(url),1000);}
export const featureId=(feature:GeoFeature)=>String(feature.properties.__id??feature.properties.uid??feature.properties.location_uid??feature.properties.spatial_uid??'');
export const valueUnit=(layer:LayerId,summary:Prepared['summary'])=>layer==='population'?'人':layer==='generator'||layer==='storage'?'MW':summary.unit||'';
