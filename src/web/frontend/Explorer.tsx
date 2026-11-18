import {useDeferredValue,useEffect,useMemo,useState} from 'react';
import {Box,Layers,Search,X,Map as MapIcon,SlidersHorizontal,Info,ArrowUpRight,RotateCcw,ChevronDown,Check} from 'lucide-react';
import type {Bootstrap,Overview,MapSettings,GeoFeature,Province,NetworkFilter} from './types';
import {useLayer,layerNames,stages,stageDescriptions,format,featureId,labels,valueUnit} from './data';
import {assetDisplayFeatures,boundsOf,isStandardGeneration,uniqueFeatures} from './geometry';
import MapCanvas from './MapCanvas';
import NetworkLegend,{NetworkActions} from './NetworkLegend';
import {AUTO_NETWORK,automaticVoltageFloor,filterNetworkFeatures,networkVoltageLevels,visibleNetworkVoltages} from './networkFilter';
import {ErrorState,Loading} from './ui';
import TermHelp from './TermHelp';
import {unitTerm} from './glossary';
import {energyStyle} from './mapStyle';

export const defaultSettings:MapSettings={stage:'standard',layer:'generator',resource:'onshore',category:'',networkFilter:AUTO_NETWORK,threeD:false,height:1,basemap:true};
export default function Explorer({config,overview,settings,setSettings,province,onProvinceConsumed}:{config:Bootstrap;overview:Overview;settings:MapSettings;setSettings:(next:MapSettings)=>void;province?:Province;onProvinceConsumed:()=>void}){
  const{data,loading,error,retry}=useLayer(settings);const[zoom,setZoom]=useState(4);const[selected,setSelected]=useState<GeoFeature>();const[focus,setFocus]=useState<{bounds:[number,number,number,number];key:string}>();const[search,setSearch]=useState('');const deferred=useDeferredValue(search);const[fallback,setFallback]=useState(false);const[filters,setFilters]=useState(false);const[legendOpen,setLegendOpen]=useState(()=>!matchMedia('(max-width:700px)').matches);
  const update=(patch:Partial<MapSettings>)=>{
    if('category' in patch || 'networkFilter' in patch) setSelected(undefined);
    setSettings({...settings,...patch});
  };
  const resetFilters=()=>update({category:'',networkFilter:AUTO_NETWORK});
  const selectNetwork=(networkFilter:NetworkFilter)=>update({networkFilter});
  useEffect(()=>{setSelected(undefined);setSearch('');},[settings.stage,settings.layer,settings.resource]);
  useEffect(()=>{if(province){setFocus({bounds:province.bounds,key:province.id});onProvinceConsumed();}},[province]);
  const network=settings.layer==='network';
  const threshold=settings.networkFilter.mode==='auto' ? automaticVoltageFloor(zoom) : 0;
  const levels=useMemo(()=>data && network ? networkVoltageLevels(data.features,data.summary) : [],[data,network]);
  const voltages=useMemo(()=>levels.map(level=>level.voltage),[levels]);
  const visibleVoltages=useMemo(()=>visibleNetworkVoltages(voltages,settings.networkFilter,threshold),[voltages,settings.networkFilter,threshold]);
  const filtered=useMemo(()=>{
    if(!data)return[];
    if(network)return filterNetworkFeatures(data.features,visibleVoltages);
    if(settings.category)return data.features.filter(f=>Number(f.properties.breakdown?.[settings.category])>0).map(f=>({...f,properties:{...f.properties,__value:f.properties.breakdown[settings.category],breakdown:{[settings.category]:f.properties.breakdown[settings.category]}}}));
    return data.features;
  },[data,network,settings.category,visibleVoltages]);
  const rendered=useMemo(()=>assetDisplayFeatures(filtered,settings,zoom),[filtered,settings.stage,settings.layer,zoom<4,zoom<6]);
  const aggregated=rendered!==filtered;
  const visibleCount=useMemo(()=>uniqueFeatures(filtered).length,[filtered]);
  const results=useMemo(()=>{
    if(!deferred.trim())return[];const query=deferred.toLowerCase().trim();const regions=overview.provinces.filter(p=>p.name.toLowerCase().includes(query)||p.id.toLowerCase().includes(query)).slice(0,5).map(p=>({id:p.id,name:p.name,bounds:p.bounds,feature:undefined as GeoFeature|undefined}));
    const seen=new Set<string>();for(const f of data?.features||[]){const id=featureId(f);if(id.toLowerCase().includes(query)&&!seen.has(id)){seen.add(id);regions.push({id,name:id,bounds:boundsOf(f.geometry),feature:f});if(regions.length>=8)break;}}return regions;
  },[deferred,data,overview]);
  const selectSearchResult=(result:typeof results[number])=>{
    setFocus({bounds:result.bounds,key:result.id+Date.now()});
    const feature=result.feature;
    if(feature && network) {
      const voltage=Number(feature.properties.voltage_kv);
      if(!visibleVoltages.has(voltage)) selectNetwork({mode:'manual',voltages:[...visibleVoltages,voltage]});
    } else if(feature && settings.category) {
      update({category:''});
    }
    setSelected(feature);
    setSearch('');
  };
  const unit=data?valueUnit(settings.layer,data.summary):'';const unitHelp=unitTerm(unit);
  const categories=data?.summary.categories||[];
  const selectedName=selected?(overview.provinces.find(p=>p.id===featureId(selected))?.name||selected.properties.name||(selected.properties.__count?'位置聚合':`${layerNames[settings.layer]}对象`)):'';
  return <div className="explorer">
    <div className="map-toolbar"><div className="field"><label className="sr-only" htmlFor="stage">数据层</label><select id="stage" aria-label="数据层" title={stageDescriptions[settings.stage]} value={settings.stage} onChange={e=>update({stage:e.target.value as MapSettings['stage'],category:'',networkFilter:AUTO_NETWORK})}>{Object.entries(stages).map(([id,label])=><option key={id} value={id}>{label}</option>)}</select><TermHelp term="dataStages" label="数据层"/></div><span className="toolbar-divider"/>
      <div className="layer-tabs" role="group" aria-label="地图图层">{Object.entries(layerNames).map(([id,name])=><button key={id} className={settings.layer===id?'active':''} aria-pressed={settings.layer===id} onClick={()=>update({layer:id as MapSettings['layer'],category:''})}>{name}</button>)}</div>
      {settings.layer==='resource' && <label className="resource-picker">
        <span className="sr-only">资源类型</span>
        <select aria-label="资源类型" value={settings.resource} onChange={e=>update({resource:e.target.value})}>
          {config.manifest.resource_classes.map(resource=><option key={resource.id} value={resource.id}>{labels[resource.id]||resource.label}</option>)}
        </select>
      </label>}
      <button className={`button compact ${filters?'selected':''}`} onClick={()=>setFilters(!filters)} aria-expanded={filters} aria-controls="map-filters"><SlidersHorizontal size={15}/>筛选</button>
    </div>
    {filters&&<div className="filter-bar" id="map-filters"><span><Layers size={14}/>{stageDescriptions[settings.stage]}</span>
      {categories.length>0&&<label>能源类型<select aria-label="能源类型" value={settings.category} onChange={e=>update({category:e.target.value})}><option value="">全部类型</option>{categories.map(c=><option value={c.id} key={c.id}>{labels[c.id]||c.label}</option>)}</select></label>}
      {network&&<NetworkActions voltages={voltages} filter={settings.networkFilter} onChange={selectNetwork} label="工具栏电压筛选"/>}
      <label className="checkbox"><input type="checkbox" checked={settings.basemap} onChange={e=>update({basemap:e.target.checked})}/>地理底图</label><button className="text-button" onClick={resetFilters}><RotateCcw size={13}/>重置筛选</button>
      {data&&<span className="filter-count term-label" aria-live="polite">{format(visibleCount)} / {format(data.sourceCount)} 个要素<TermHelp term="drawable"/>{aggregated&&<> · {format(rendered.length)} 个聚合位置<TermHelp term="displayAggregation"/></>}</span>}
    </div>}
    <div className={`map-workspace ${selected?'has-detail':''}`}>
      <div className="map-main">
        <div className="map-floating-top"><div className="map-search"><Search size={16}/><input aria-label="搜索省份或对象 ID" placeholder="搜索省份或对象 ID" value={search} onChange={e=>setSearch(e.target.value)} onKeyDown={e=>{if(e.key==='Escape')setSearch('');if(e.key==='Enter'&&results[0])selectSearchResult(results[0]);}}/>{search&&<button aria-label="清除搜索" onClick={()=>setSearch('')}><X size={14}/></button>}
          {search&&<div className="search-results" aria-live="polite">{results.length?results.map(r=><button key={r.id} onClick={()=>selectSearchResult(r)}><span>{r.name}</span><ArrowUpRight size={14}/></button>):<p>没有匹配结果。可输入省名或已有对象 ID。</p>}</div>}
        </div><div className="view-toggle" role="group" aria-label="地图维度"><button aria-pressed={!settings.threeD||fallback} className={!settings.threeD||fallback?'active':''} onClick={()=>update({threeD:false})}><MapIcon size={14}/>2D</button><button disabled={fallback} title={fallback?'当前浏览器使用兼容 2D 渲染':'切换三维数据地图'} aria-pressed={settings.threeD&&!fallback} className={settings.threeD&&!fallback?'active':''} onClick={()=>update({threeD:true})}><Box size={14}/>3D</button></div></div>
        <MapCanvas config={config} features={rendered} settings={settings} summary={data?.summary||{count:0}} max={data?.max||1} selected={selected?featureId(selected):undefined} focus={focus} onSelect={setSelected} onZoom={setZoom} onFallback={setFallback}/>
        {loading&&<div className="map-loading"><Loading text={`正在加载${layerNames[settings.layer]}数据…`}/><small>大图层在后台处理，你仍可切换页面。</small></div>}
        {error&&<div className="map-loading"><ErrorState message={error} retry={retry}/></div>}
        {!loading&&!error&&!rendered.length&&<div className="map-empty"><strong>当前筛选没有数据</strong><button className="button" onClick={resetFilters}>{network?'恢复自动分级':'清除筛选'}</button></div>}
        {data&&<div className={`map-legend ${legendOpen?'expanded':'collapsed'}`}><button className="legend-heading" aria-expanded={legendOpen} aria-controls="map-legend-content" onClick={()=>setLegendOpen(!legendOpen)}><Layers size={15}/><strong>{network?'电压等级':settings.layer==='resource'?labels[settings.resource]:layerNames[settings.layer]}</strong><ChevronDown size={15} style={{transform:legendOpen?'rotate(180deg)':undefined}}/></button><div id="map-legend-content" hidden={!legendOpen}>
          {network ? <NetworkLegend levels={levels} voltages={voltages} filter={settings.networkFilter} visible={visibleVoltages} minimum={threshold} onChange={selectNetwork}/>
          : categories.length ? <>
            <div className="legend-actions-row"><button type="button" className="legend-all" aria-pressed={!settings.category} onClick={()=>update({category:''})}>全选</button></div>
            <div className="legend-categories energy-categories">
              {categories.map(category=>{
                const style=energyStyle(category.id,category.color);
                const active=settings.category===category.id;
                return <button type="button" key={category.id} className={settings.category&&!active?'dim':''} aria-pressed={active} onClick={()=>update({category:active?'':category.id})}>
                  <i style={{background:style.fill,borderColor:style.stroke}} aria-hidden="true"/>
                  <span>{labels[category.id]||category.label}</span>
                  <Check className="legend-check" size={12} visibility={active?'visible':'hidden'} aria-hidden="true"/>
                </button>;
              })}
            </div>
          </> : <>
            <div className="color-ramp"/>
            <div className="legend-range"><span>0</span><span>{format(data.max,settings.layer==='resource'?2:1)}</span></div>
            <small className="term-label">{settings.layer==='population'?<>颜色：log10(人口 + 1)<TermHelp term="logPopulation"/></>:<>{unit}{unitHelp&&<TermHelp term={unitHelp}/>}</>}</small>
          </>}
          {settings.threeD&&!fallback&&settings.layer!=='network'&&<label className="height-control"><span className="term-label">高度比例<TermHelp term="height"/></span><span>{settings.height.toFixed(1)}×</span><input aria-label="高度比例" type="range" min="0.2" max="3" step="0.2" value={settings.height} onChange={e=>update({height:Number(e.target.value)})}/><small>高度编码：{settings.layer==='population'?'log10(人口 + 1)':unit}</small></label>}
        </div></div>}
      </div>
      {selected&&data&&<aside className="detail-panel" aria-label="对象详情"><div className="detail-heading"><span>对象详情</span><button className="icon-button" aria-label="关闭详情" onClick={()=>setSelected(undefined)}><X size={18}/></button></div><h2>{selectedName}</h2><div className="detail-id"><code>{featureId(selected)}</code><TermHelp term="stableId"/></div><div className="detail-value">{format(settings.layer==='network'?selected.properties.voltage_kv:selected.properties.__value,2)}<span>{settings.layer==='network'?'kV':unit}</span></div>
        {selected.properties.__count&&<p className="muted">包含 {format(selected.properties.__count)} 个位置；放大地图可查看明细。</p>}
        <dl className="detail-meta"><div><dt>数据层</dt><dd>{stages[settings.stage]}<TermHelp term={settings.stage}/></dd></div><div><dt>年份</dt><dd>{overview.year}</dd></div>
          {isStandardGeneration(settings) && selected.geometry.type==='Point' && <div><dt>经度 / 纬度</dt><dd>{selected.geometry.coordinates.slice(0,2).map(value=>`${format(value,6)}°`).join(' / ')}</dd></div>}
          {network&&<><div><dt>对象类型</dt><dd>{selected.properties.feature_kind==='bus'?'母线':'支路'}<TermHelp term={selected.properties.feature_kind==='bus'?'bus':'branch'}/></dd></div><div><dt>电流类型</dt><dd>{selected.properties.current_type||'未提供'}</dd></div></>}</dl>
        {selected.properties.breakdown&&<><h3>容量构成</h3>{Object.entries(selected.properties.breakdown).sort((a,b)=>Number(b[1])-Number(a[1])).map(([name,value])=><div className="detail-row" key={name}><span>{labels[name]||name}</span><strong>{format(Number(value),2)} <small>MW</small></strong></div>)}</>}
        <div className="note"><Info size={15}/><p>{settings.layer==='population'?'这里展示实际人数；地图的颜色与高度采用对数刻度，便于观察数量级差异。':settings.layer==='load'?'该图层展示全年用电量，单位 TWh。运行模拟中的逐小时负荷使用 MW。':'数据来自已导入的数据包。对象 ID 保留原始来源标识。'}</p></div>
      </aside>}
    </div>
  </div>;
}
