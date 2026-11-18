import {lazy,Suspense,useEffect,useMemo,useRef,useState} from 'react';
import * as maplibregl from 'maplibre-gl';
import {MapboxOverlay} from '@deck.gl/mapbox';
import {GeoJsonLayer,ColumnLayer,ScatterplotLayer} from '@deck.gl/layers';
import {Plus,Minus,Compass,RotateCcw} from 'lucide-react';
import type {FeatureCollection} from 'geojson';
import type {GeoFeature,MapSettings,Summary,Bootstrap} from './types';
import {duration,featureId,format,request,valueUnit} from './data';
import {featureColor,pointFillColor,pointRadius} from './mapStyle';
import {isStandardGeneration} from './geometry';
import 'maplibre-gl/dist/maplibre-gl.css';
const LeafletMap=lazy(()=>import('./LeafletMap'));
export interface MapProps {features:GeoFeature[];settings:MapSettings;summary:Summary;max:number;config:Bootstrap;selected?:string;focus?:{bounds:[number,number,number,number];key:string};onSelect:(feature:GeoFeature)=>void;onZoom?:(zoom:number)=>void;onFallback?:(active:boolean)=>void;compact?:boolean;forceFallback?:boolean}
export default function MapCanvas(props:MapProps){
  const ref=useRef<HTMLDivElement>(null);const map=useRef<maplibregl.Map|undefined>(undefined);const overlay=useRef<MapboxOverlay|undefined>(undefined);const callbacks=useRef(props);callbacks.current=props;
  const[fallback,setFallback]=useState(props.forceFallback||new URLSearchParams(location.search).get('renderer')==='leaflet');const[ready,setReady]=useState(false);const[viewZoom,setViewZoom]=useState(4);const[boundary,setBoundary]=useState<FeatureCollection>();
  const[hover,setHover]=useState<{x:number;y:number;feature:GeoFeature;source:GeoFeature[]}>();
  const activeHover=hover?.source===props.features ? hover : undefined;
  const assets=props.settings.layer==='generator'||props.settings.layer==='storage';
  const {points,surfaces,maxValue}=useMemo(()=>({
    points:assets?props.features:props.features.filter(feature=>feature.geometry.type==='Point'),
    surfaces:assets?[]:props.features.filter(feature=>feature.geometry.type!=='Point'),
    maxValue:assets?props.features.reduce((max,feature)=>Math.max(max,feature.properties.__value),1):Math.max(props.max,1e-8),
  }),[assets,props.features,props.max]);
  const marked=useMemo(()=>props.selected?props.features.filter(feature=>featureId(feature)===props.selected):[],[props.features,props.selected]);
  useEffect(()=>setHover(undefined),[props.features]);
  useEffect(()=>{request<FeatureCollection>('/api/boundary/standard').then(setBoundary).catch(()=>{});},[]);
  useEffect(()=>{props.onFallback?.(fallback);},[fallback]);
  useEffect(()=>{
    if(fallback)return;
    try{
      const instance=new maplibregl.Map({container:ref.current!,style:{version:8,sources:{},layers:[{id:'background',type:'background',paint:{'background-color':'#eef2f0'}}]},center:[104,35.5],zoom:3.4,minZoom:2,maxZoom:13,maxPitch:65,attributionControl:false,canvasContextAttributes:{antialias:true},renderWorldCopies:false});
      map.current=instance;
      const deck=new MapboxOverlay({interleaved:false,layers:[],useDevicePixels:1,onError:()=>setFallback(true)});overlay.current=deck;instance.addControl(deck as any);
      instance.on('load',()=>{instance.fitBounds([[73,17],[135,54]],{padding:props.compact?22:50,duration:0});setReady(true);});
      instance.on('zoomend',()=>{setViewZoom(instance.getZoom());callbacks.current.onZoom?.(instance.getZoom());});
      instance.getCanvas().addEventListener('webglcontextlost',()=>setFallback(true));
      const resize=new ResizeObserver(()=>instance.resize());resize.observe(ref.current!);
      return()=>{resize.disconnect();instance.remove();map.current=undefined;overlay.current=undefined;};
    }catch{setFallback(true);}
  },[fallback]);
  useEffect(()=>{if(ready&&map.current)map.current.easeTo({pitch:props.settings.threeD?45:0,duration:duration()});},[ready,props.settings.threeD]);
  useEffect(()=>{if(ready&&props.focus&&map.current){const b=props.focus.bounds;map.current.fitBounds([[b[0],b[1]],[b[2],b[3]]],{padding:props.compact?35:70,maxZoom:8,duration:duration()});}},[props.focus,ready]);
  useEffect(()=>{
    if(!overlay.current||!ready)return;
    const{features,settings,summary,max,selected}=props;
    const network=settings.layer==='network';
    const color=(f:GeoFeature)=>featureColor(f,settings,summary,max);
    const picking={pickable:true,autoHighlight:!assets,highlightColor:[103,145,126,120] as [number,number,number,number],onClick:(info:any)=>{if(info.object)callbacks.current.onSelect(info.object);},onHover:(info:any)=>setHover(info.object?{x:info.x,y:info.y,feature:info.object,source:features}:undefined)};
    const height=(f:GeoFeature)=>(assets?f.properties.__value:f.properties.__colorValue)/maxValue*Math.min(400000,400000/2**(viewZoom-4))*settings.height;
    const columnRadius=assets?(viewZoom<6?(isStandardGeneration(settings)?4500:18000):1500):4000;
    const layers:any[]=[];
    if(boundary&&(settings.basemap||props.compact))layers.push(new GeoJsonLayer({id:'boundaries',data:boundary,stroked:true,filled:false,getLineColor:[128,155,143,140],getLineWidth:.6,lineWidthUnits:'pixels',pickable:false}));
    if(surfaces.length)layers.push(new GeoJsonLayer({...picking,id:`surface-${settings.layer}`,data:surfaces,filled:!network,stroked:true,extruded:settings.threeD&&!network,wireframe:false,getElevation:height,getFillColor:color,getLineColor:network?color:[159,189,173,125],getLineWidth:network?1:.35,lineWidthUnits:'pixels',lineWidthMinPixels:network?.8:.25,opacity:network?.8:.9,updateTriggers:{getFillColor:[settings.category,max],getLineColor:[settings.category,max],getElevation:[settings.height,maxValue,viewZoom]}}));
    if(points.length){
      if(settings.threeD&&!network)layers.push(new ColumnLayer({...picking,id:`columns-${settings.layer}`,data:points,diskResolution:6,radius:columnRadius,getPosition:(f:GeoFeature)=>f.properties.__position,getElevation:height,getFillColor:color,extruded:true,stroked:false,opacity:.9,updateTriggers:{getElevation:[settings.height,maxValue,viewZoom],getFillColor:[settings.category,max]}}));
      else layers.push(new ScatterplotLayer({...picking,id:`points-${settings.layer}`,data:points,getPosition:(f:GeoFeature)=>f.properties.__position,getFillColor:(f:GeoFeature)=>pointFillColor(f,settings,summary,max),getRadius:(f:GeoFeature)=>pointRadius(f,settings,assets?maxValue:max,viewZoom),radiusUnits:'pixels',radiusMinPixels:network?0.9:1.5,stroked:!network,getLineColor:assets?color:[255,255,255,160],lineWidthUnits:'pixels',getLineWidth:assets?1.15:.6,updateTriggers:{getFillColor:[settings.category,max],getLineColor:[settings.category,max],getRadius:[maxValue,max,viewZoom]}}));
    }
    const hovered=assets?activeHover?.feature:undefined;
    if(hovered&&featureId(hovered)!==selected)layers.push(new GeoJsonLayer({id:'asset-hover',data:[hovered],filled:false,stroked:true,getLineColor:[73,94,84,220],getLineWidth:1.5,lineWidthUnits:'pixels',pointRadiusUnits:'pixels',getPointRadius:(f:GeoFeature)=>pointRadius(f,settings,maxValue,viewZoom)+2.5,pickable:false}));
    if(marked.length)layers.push(new GeoJsonLayer({id:'selection',data:marked,filled:false,stroked:true,getLineColor:[22,51,43,255],getLineWidth:2,lineWidthUnits:'pixels',pointRadiusUnits:'pixels',getPointRadius:(f:GeoFeature)=>assets?pointRadius(f,settings,maxValue,viewZoom)+3.5:9,pickable:false}));
    overlay.current.setProps({layers});
  },[ready,boundary,viewZoom,props.features,props.settings,props.summary,props.max,props.selected,activeHover?.feature,points,surfaces,maxValue,marked,assets]);
  if(fallback)return <div className="map-canvas"><Suspense fallback={<div className="map-loading">正在加载兼容地图…</div>}><LeafletMap {...props} boundary={boundary}/></Suspense><div className="map-renderer">兼容 2D · Leaflet</div></div>;
  return <div className="map-canvas" data-renderer="webgl"><div ref={ref} className="map-host" role="region" aria-label="交互式电力地图"/>
    <div className="map-attribution">行政边界 · 本地导出数据</div><div className="map-navigation"><button title="放大" aria-label="放大地图" onClick={()=>map.current?.zoomIn({duration:duration()})}><Plus size={17}/></button><button title="缩小" aria-label="缩小地图" onClick={()=>map.current?.zoomOut({duration:duration()})}><Minus size={17}/></button><button title="恢复北向" aria-label="恢复北向" onClick={()=>map.current?.easeTo({bearing:0,duration:duration()})}><Compass size={17}/></button><button title="全国视图" aria-label="恢复全国视图" onClick={()=>map.current?.fitBounds([[73,17],[135,54]],{padding:40,duration:duration()})}><RotateCcw size={16}/></button></div>
    {props.settings.threeD&&<div className="map-help">右键拖动旋转 · 滚轮缩放</div>}
    {activeHover&&<div className="map-tooltip" style={{left:Math.max(8,Math.min(activeHover.x+14,(ref.current?.clientWidth||500)-250)),top:Math.max(8,activeHover.y-72)}}><strong>{activeHover.feature.properties.__count?`${format(activeHover.feature.properties.__count)} 个位置聚合`:activeHover.feature.properties.name||featureId(activeHover.feature)}</strong><span>{props.settings.layer==='network'?`${format(activeHover.feature.properties.voltage_kv)} kV · ${activeHover.feature.properties.current_type||'—'}`:`${format(activeHover.feature.properties.__value,2)} ${valueUnit(props.settings.layer,props.summary)}`}</span><small>点击查看详情</small></div>}
  </div>;
}
