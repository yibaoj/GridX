import {useEffect,useMemo,useRef,useState} from 'react';
import {Download,Expand,Shrink,Image,ArrowRight} from 'lucide-react';
import type {EChartsType,EChartsCoreOption} from 'echarts/core';
import type {Application,RegionalOverview} from './types';
import {useResource,labels,format,dateLabel,download} from './data';
import Chart,{renderChartPng} from './Chart';
import {simulationCSV,seriesTitle,firstDayEnd} from './exportSimulation';
import {Loading,ErrorState,useModalFocus} from './ui';
import TermHelp from './TermHelp';
import {energyStyle} from './mapStyle';

type SimulationView = EChartsCoreOption & {
  grid?:Record<string,unknown>[];
  legend?:{selected?:Record<string,boolean>}[];
  dataZoom?:{type?:string;start?:number;end?:number}[];
};

function seriesColor(data:Application,series:Application['series'][number]) {
  if(series.kind==='load') return '#263b33';
  return energyStyle(series.id,data.categories.find(category=>category.id===series.id)?.color||'#8d9e95').stroke;
}

export function dispatchOption(data:Application,compact=false,start=0,end=100):EChartsCoreOption{return{
  grid:{left:compact?54:64,right:24,top:26,bottom:compact?30:86},tooltip:{trigger:'axis',confine:true,order:'valueDesc',valueFormatter:(v:any)=>`${format(Number(v),1)} MW`},
  legend:{show:false},
  xAxis:{type:'category',data:data.time,boundaryGap:false,axisLabel:{formatter:(v:string)=>v.slice(5,10)+'\n'+v.slice(11,16),fontSize:11},axisLine:{lineStyle:{color:'#dce3de'}},axisTick:{show:false}},
  yAxis:{type:'value',name:'MW',axisLabel:{formatter:(v:number)=>Math.abs(v)>=1000?`${format(v/1000)}k`:format(v)},splitLine:{lineStyle:{color:'#edf0ee',type:'dashed'}}},
  dataZoom:compact?[]:[{type:'inside',start,end},{type:'slider',bottom:16,height:24,start,end,borderColor:'#e1e6e2',fillerColor:'rgba(36,120,94,.12)',handleStyle:{color:'#24785e'}}],
  series:data.series.map(s=>{const line=s.kind==='load';const charge=s.kind==='storage_charge';const color=seriesColor(data,s);return{
    id:`${s.kind}:${s.id}`,name:seriesTitle(s,labels[s.id]||s.id),type:'line',data:s.values,symbol:'none',stack:line?undefined:charge?'charging':'supply',lineStyle:{width:line?2:0,color},areaStyle:line?undefined:{color,opacity:.88},itemStyle:{color},emphasis:{focus:'series'},z:line?10:2};})};}
export default function Simulation({compact=false,onOpen,onExpandedChange}:{compact?:boolean;onOpen?:()=>void;onExpandedChange?:(expanded:boolean)=>void}){
  const{data,error,retry}=useResource<Application>('/api/application/uc');const regional=useResource<RegionalOverview>('/api/regional-overview');const[expanded,setExpandedState]=useState(false);const[range,setRange]=useState('all');const[showTable,setShowTable]=useState(false);const[exportStatus,setExportStatus]=useState('');const[seriesVisibility,setSeriesVisibility]=useState<Record<string,boolean>>({});const visibility=useRef(seriesVisibility);visibility.current=seriesVisibility;const chart=useRef<EChartsType|undefined>(undefined);
  const setExpanded=(next:boolean)=>{setExpandedState(next);onExpandedChange?.(next);};
  useEffect(()=>()=>onExpandedChange?.(false),[onExpandedChange]);
  const dialog=useRef<HTMLDivElement>(null);const expandButton=useRef<HTMLButtonElement>(null);
  useModalFocus(expanded,dialog,()=>setExpanded(false),()=>expandButton.current);
  const option=useMemo(()=>{if(!data)return{};const next=dispatchOption(data,compact,0,firstDayEnd(data.time.length,range==='day'));if(!compact)next.legend={...(next.legend as object),selected:visibility.current};return next;},[data,compact,range]);
  const exportCSV=()=>{
    if(!data || !chart.current) return;
    const current=chart.current.getOption() as SimulationView;
    const zoom=current.dataZoom?.[0];
    const csv=simulationCSV(data,zoom?.start??0,zoom?.end??100,current.legend?.[0]?.selected,labels);
    download('GridX-运行模拟-当前视图.csv',csv.content);
    setExportStatus(`已生成 CSV：${csv.hours} 小时、${csv.series} 个系列。`);
  };
  const exportPNG=()=>{
    const instance=chart.current;
    if(!instance) return;
    const current=instance.getOption() as SimulationView;
    // The interactive legend is HTML. Draw its selected state into the exported image.
    const png=renderChartPng({
      ...current,
      grid:[{...current.grid?.[0],top:38,bottom:134}],
      legend:[{...current.legend?.[0],show:true,bottom:12,left:20,right:20,itemWidth:10,itemHeight:8,textStyle:{fontSize:12}}],
      dataZoom:current.dataZoom?.map(zoom=>zoom.type==='slider'?{...zoom,bottom:56}:zoom),
    },Math.max(760,instance.getWidth()),instance.getHeight()+48);
    const link=document.createElement('a');
    link.href=png;
    link.download='GridX-运行模拟.png';
    document.body.append(link);
    link.click();
    link.remove();
    setExportStatus('已生成 PNG 图片。');
  };
  if(compact) return <section className="panel">
    <div className="panel-heading"><div><h2>运行模拟</h2><span>{data?format(data.time.length):'—'} 小时 · 本地已保存结果</span></div><button className="text-button" onClick={onOpen}>查看详情<ArrowRight size={15}/></button></div>
    {error?<ErrorState message={error} retry={retry}/>:data?<Chart option={option} label="本地供需模拟概览" height={265}/>:<Loading/>}
  </section>;
  if(error)return <ErrorState message={error} retry={retry}/>;if(!data)return <Loading/>;
  return <div ref={dialog} tabIndex={-1} role={expanded?'dialog':undefined} aria-modal={expanded?true:undefined} aria-labelledby={expanded?'simulation-chart-title':undefined} className={`simulation ${expanded?'chart-expanded':''}`}>
    {regional.error&&<ErrorState message={regional.error} retry={regional.retry}/>}
    <div className="simulation-metrics"><div><span>装机容量<TermHelp term="simulationCapacity"/></span><strong>{format(regional.data?.simulation.installed_capacity_mw!=null?regional.data.simulation.installed_capacity_mw/1000:undefined,1)} <small>GW</small></strong></div><div><span>模拟最大负荷<TermHelp term="simulationPeak"/></span><strong>{format(regional.data?.simulation.maximum_load_mw!=null?regional.data.simulation.maximum_load_mw/1000:undefined,1)} <small>GW</small></strong></div><div><span>模拟时长</span><strong>{format(data.time.length)} <small>小时</small></strong></div><div><span>网络范围<TermHelp term="voltage"/></span><strong>{format(data.summary.case_minimum_voltage_kv)} <small>kV+</small></strong></div><div><span>未供电量占比<TermHelp term="unserved"/></span><strong>{format(data.summary.load_shedding_share*100,4)}<small>%</small></strong></div></div>
    <section className="panel"><div className="panel-heading"><div><h2 id="simulation-chart-title" className="term-label">电力供需构成<TermHelp term="powerMW"/></h2><span>{dateLabel(data.time[0])} — {dateLabel(data.time.at(-1)!)}</span></div><div className="chart-actions"><select aria-label="模拟时间范围" value={range} onChange={e=>setRange(e.target.value)}><option value="all">完整时段（{format(data.time.length)} 小时）</option><option value="day">首日 24 小时</option></select><button className="icon-button" title="导出当前图表 CSV" aria-label="导出当前图表 CSV" onClick={exportCSV}><Download size={17}/></button><button className="icon-button" title="导出图表图片" aria-label="导出图表图片" onClick={exportPNG}><Image size={17}/></button><button ref={expandButton} className="icon-button" title={expanded?'退出放大':'放大图表'} aria-label={expanded?'退出放大':'放大图表'} onClick={()=>setExpanded(!expanded)}>{expanded?<Shrink size={17}/>:<Expand size={17}/>}</button></div></div>
      <Chart option={option} onReady={c=>{chart.current=c;c.on('legendselectchanged',(event:any)=>setSeriesVisibility(event.selected));}} height={expanded?Math.max(350,window.innerHeight-245):440} label="电力供需时序图，单位 MW；支持缩放与系列显隐"/>
      <div className="simulation-legend" role="group" aria-label="图表系列">
        {data.series.map(series=>{
          const name=seriesTitle(series,labels[series.id]||series.id);
          return <button type="button" key={`${series.kind}:${series.id}`} aria-pressed={seriesVisibility[name]!==false}
            onClick={()=>chart.current?.dispatchAction({type:'legendToggleSelect',name})}>
            <i style={{backgroundColor:seriesColor(data,series)}} aria-hidden="true"/>{name}
          </button>;
        })}
      </div>
      <div className="chart-footnote"><TermHelp term="chartInteraction" label="图表操作"/><button className="text-button" onClick={()=>setShowTable(!showTable)}>{showTable?'收起数据表':'查看数据表'}</button></div>
    </section>
    {exportStatus&&<p className="export-status" role="status">{exportStatus}</p>}
    {showTable&&<section className="panel"><div className="panel-heading"><h2>逐小时原始功率</h2><span>MW · 全部 {format(data.time.length)} 小时</span></div><div className="data-table-scroll"><table><thead><tr><th>时间</th>{data.series.map(s=><th key={`${s.id}:${s.kind}`}>{labels[s.id]||s.id}{s.kind==='storage_charge'?'（充电）':s.kind==='storage_discharge'?'（放电）':''}</th>)}</tr></thead><tbody>{data.time.map((t,i)=><tr key={t}><th>{dateLabel(t)}</th>{data.series.map(s=><td key={`${s.id}:${s.kind}`}>{format(s.values[i],2)}</td>)}</tr>)}</tbody></table></div></section>}
    {!expanded&&<details className="simulation-explanation"><summary>计算与图表说明</summary><div className="simulation-notes"><div><h3 className="term-label">如何读这张图<TermHelp term="load"/></h3><p>彩色面积表示不同技术的发电与储能功率，深色线表示负荷。储能充电显示在零线以下；“未供电负荷”表示供需缺口。</p></div><div><h3 className="term-label">计算口径<TermHelp term="rollingHorizon"/></h3><p>{data.summary.solve_mode==='rolling_horizon'?`滚动求解，每个窗口 ${data.summary.rolling_horizon_hours} 小时。`: '整段时序求解。'}求解状态：{data.summary.termination_condition==='optimal'?'最优解':data.summary.termination_condition}。页面读取预先生成的结果，保留原始时间标记。时间标记沿用本地导出结果。</p></div></div></details>}
  </div>;
}
