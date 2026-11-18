import {useMemo,useState} from 'react';
import {ArrowDown,ArrowUpRight,ArrowRight,Search,Zap,Sun,GitBranch,Gauge} from 'lucide-react';
import type {Bootstrap,Overview as OverviewData,RegionalOverview,Province,GeoFeature,LayerId} from './types';
import {format,useLayer,useResource,labels} from './data';
import {defaultSettings} from './Explorer';
import {provincialStats} from './overviewScope';
import MapCanvas from './MapCanvas';
import Chart from './Chart';
import Simulation from './Simulation';
import {Loading,ErrorState} from './ui';
import TermHelp from './TermHelp';
import type {GlossaryTerm} from './glossary';
import {energyStyle} from './mapStyle';
const metricHelp:Record<string,GlossaryTerm>={'装机容量':'regionalCapacity','新能源装机':'newEnergyCapacity','年用电量':'energyTWh','最大负荷':'annualPeak','特高压支路数':'uhvBranches'};
const miniSettings={...defaultSettings,layer:'load' as const,basemap:false};
export default function Overview({config,data,province,onProvince,onMap,onSimulation}:{config:Bootstrap;data:OverviewData;province?:Province;onProvince:(p?:Province)=>void;onMap:(p?:Province,layer?:LayerId)=>void;onSimulation:()=>void}){
  const[query,setQuery]=useState('');const[ascending,setAscending]=useState(false);const{data:mapData,error,retry}=useLayer(miniSettings);const regional=useResource<RegionalOverview>('/api/regional-overview');
  const rows=useMemo(()=>data.provinces.filter(p=>p.name.includes(query.trim())).sort((a,b)=>ascending?a.value-b.value:b.value-a.value),[data,query,ascending]);
  const previewFocus=useMemo(()=>province?{bounds:province.bounds,key:province.id}:{bounds:[73,17,135,54] as [number,number,number,number],key:'national'},[province]);
  const stats=province?provincialStats(data,province):undefined;
  const peers=stats?stats.ranked.filter((p,i)=>i<5||p.id===province!.id):[];
  const peerOption={grid:{left:95,right:65,top:14,bottom:26},tooltip:{trigger:'axis',confine:true,valueFormatter:(v:any)=>`${format(v,1)} TWh`},xAxis:{type:'value',name:'TWh',axisLabel:{fontSize:11},splitLine:{lineStyle:{color:'#edf0ee',type:'dashed'}}},yAxis:{type:'category',inverse:true,data:peers.map(p=>p.name),axisTick:{show:false},axisLine:{show:false},axisLabel:{fontSize:12}},series:[{type:'bar',barMaxWidth:21,label:{show:true,position:'right',formatter:(p:any)=>format(p.value,1),color:'#40564a',fontSize:12},data:peers.map(p=>({value:p.value,itemStyle:{color:p.id===province?.id?'#24785e':'#b5cdbf',borderRadius:[0,3,3,0]}}))}]};
  const categories=data.capacity_mix;const chartOption=useMemo(()=>({grid:{left:62,right:55,top:12,bottom:22},tooltip:{trigger:'axis',confine:true,valueFormatter:(v:any)=>`${format(v,1)} GW`},xAxis:{type:'value',name:'GW',axisLabel:{fontSize:11},splitLine:{lineStyle:{color:'#edf0ee',type:'dashed'}}},yAxis:{type:'category',data:categories.map(c=>labels[c.id]||c.label).reverse(),axisLine:{show:false},axisTick:{show:false},axisLabel:{fontSize:12}},series:[{type:'bar',data:categories.map(c=>({value:(c.value||0)/1000,itemStyle:{color:energyStyle(c.id,c.color).stroke,borderRadius:[0,3,3,0]}})).reverse(),barMaxWidth:16}]}),[categories]);
  const indicators=province?regional.data?.provinces[province.id]:regional.data?.national;
  const metrics=[{name:'装机容量',value:indicators?indicators.installed_capacity_mw/1000:undefined,unit:'GW',icon:Zap},{name:'新能源装机',value:indicators?indicators.new_energy_capacity_mw/1000:undefined,unit:'GW',icon:Sun},{name:'年用电量',value:province?.value??data.annual_energy_twh,unit:'TWh',icon:Zap},{name:'最大负荷',value:indicators?.peak_load_mw!=null?indicators.peak_load_mw/1000:undefined,unit:'GW',icon:Gauge,missing:true},{name:'特高压支路数',value:indicators?.uhv_branch_count,unit:'条',icon:GitBranch}];
  return <div className="overview">
    {regional.error&&<ErrorState message={regional.error} retry={regional.retry}/>}
    <section className="metric-strip regional-metrics" aria-label={`${province?.name||'全国'}关键指标`}>{metrics.map(m=><div className="metric" key={m.name}><span className="metric-label"><m.icon size={16}/>{m.name}<TermHelp term={metricHelp[m.name]}/></span>{m.missing&&m.value==null?<strong className="metric-missing">暂无数据</strong>:<strong>{format(m.value,m.unit==='条'?0:1)}<small>{m.unit}</small></strong>}</div>)}</section>
    <div className="overview-grid">
      <section className="panel province-panel">
        <div className="panel-heading">
          <div>
            <h2>各省年度用电量</h2>
            <span className="term-label">{stats?`全国第 ${stats.rank} 位 · 占 ${format(stats.share,1)}% · TWh`:`${data.provinces.length} 个地区 · TWh · 全国占比`}<TermHelp term="energyTWh"/></span>
          </div>
          <div className="inline-search"><Search size={14}/><input aria-label="筛选省份" placeholder="筛选省份" value={query} onChange={e=>setQuery(e.target.value)}/></div>
        </div>
        <div className="province-table">
          <table>
            <thead><tr><th>地区</th><th><button onClick={()=>setAscending(!ascending)} aria-label="切换用电量排序">用电量<ArrowDown size={12} style={{transform:ascending?'rotate(180deg)':undefined}}/></button></th><th>占比</th><th><span className="sr-only">查看地图</span></th></tr></thead>
            <tbody>{rows.map(p=><tr key={p.id} className={province?.id===p.id?'selected-province':''} aria-current={province?.id===p.id?true:undefined}>
              <td><button className="province-link" aria-label={`查看${p.name}数据总览`} onClick={()=>onProvince(p)}><span className="rank">{provincialStats(data,p).rank}</span>{p.name}</button></td>
              <td className="numeric">{format(p.value,1)}</td>
              <td className="share-cell"><div className="share-track"><i style={{width:`${p.value/data.provinces[0].value*100}%`}}/></div><span>{format(p.value/data.annual_energy_twh*100,1)}%</span></td>
              <td><button className="table-open" aria-label={`在地图查看${p.name}`} onClick={()=>onMap(p,'load')}><ArrowUpRight size={15}/></button></td>
            </tr>)}</tbody>
          </table>
          {!rows.length&&<p className="empty-copy">没有匹配省份，请调整关键词。</p>}
        </div>
        <div className="table-footer"><span>全国省级数据合计</span><strong>{format(data.annual_energy_twh,1)} <small>TWh</small></strong></div>
      </section>
      <section className="panel preview-map-panel">
        <div className="panel-heading"><div><h2>{province?`${province.name}用电量分布`:'用电量空间分布'}</h2><span>点击区域，查看该地区数据总览</span></div><button className="text-button" onClick={()=>onMap(province,'load')}>打开地图<ArrowUpRight size={15}/></button></div>
        <div className="preview-map">
          {mapData?<MapCanvas config={config} features={mapData.features} settings={miniSettings} summary={mapData.summary} max={mapData.max} selected={province?.id} focus={previewFocus} onSelect={(f:GeoFeature)=>{const p=data.provinces.find(x=>x.id===f.properties.__id);if(p)onProvince(p);}} compact/>:error?<ErrorState message={error} retry={retry}/>:<Loading/>}
          <div className="preview-legend"><span>低</span><div className="color-ramp"/><span>高</span><small>TWh</small></div>
        </div>
      </section>
      {province?<section className="panel overview-peer">
        <div className="panel-heading"><div><h2>用电量省际对比</h2><span>全国前五名{stats!.rank>5?'与当前省份':''} · TWh</span></div><button className="text-button" onClick={()=>onProvince()}>全国总览<ArrowRight size={15}/></button></div>
        <Chart option={peerOption} label={`${province.name}与全国用电量前五名对比，单位 TWh`} height={265}/>
      </section>:<>
        <section className="panel">
          <div className="panel-heading"><div><h2 className="term-label">发电容量构成<TermHelp term="regionalCapacity"/></h2><span>可定位记录 · GW</span></div><button className="text-button" onClick={()=>onMap(undefined,'generator')}>探索发电<ArrowUpRight size={15}/></button></div>
          <Chart option={chartOption} label="各发电技术的可定位容量比较，单位 GW" height={265}/>
        </section>
        <Simulation compact onOpen={onSimulation}/>
      </>}
    </div>
  </div>;
}
