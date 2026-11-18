import {useEffect,useMemo,useRef} from 'react';
import L from 'leaflet';
import type {FeatureCollection} from 'geojson';
import type {MapProps} from './MapCanvas';
import type {GeoFeature} from './types';
import {featureColor,pointFillColor,pointRadius} from './mapStyle';
import {featureId} from './data';
import 'leaflet/dist/leaflet.css';
export default function LeafletMap(props:MapProps&{boundary?:FeatureCollection}){
  const ref=useRef<HTMLDivElement>(null);const map=useRef<L.Map|undefined>(undefined);const callbacks=useRef(props);callbacks.current=props;
  const assets=props.settings.layer==='generator'||props.settings.layer==='storage';
  const network=props.settings.layer==='network';
  const max=useMemo(()=>assets?props.features.reduce((largest,feature)=>Math.max(largest,feature.properties.__value),1):props.max,[assets,props.features,props.max]);
  useEffect(()=>{const instance=L.map(ref.current!,{preferCanvas:true,zoomControl:false,attributionControl:false}).setView([35.5,104],4);map.current=instance;L.control.zoom({position:'topright'}).addTo(instance);instance.on('zoomend',()=>callbacks.current.onZoom?.(instance.getZoom()));const observer=new ResizeObserver(()=>instance.invalidateSize());observer.observe(ref.current!);return()=>{observer.disconnect();instance.remove();};},[]);
  useEffect(()=>{
    if(!map.current||!props.boundary||(!props.settings.basemap&&!props.compact))return;
    const boundary=L.geoJSON(props.boundary,{style:{color:'#a0b8ac',weight:.6,fill:false},interactive:false}).addTo(map.current);
    return()=>{boundary.remove();};
  },[props.boundary,props.settings.basemap,props.compact]);
  useEffect(()=>{
    if(!map.current)return;const instance=map.current;
    const cssColor=(c:number[])=>`rgb(${c[0]},${c[1]},${c[2]})`;
    const color=(f:GeoFeature)=>cssColor(featureColor(f,props.settings,props.summary,props.max));
    const fill=(f:GeoFeature)=>cssColor(pointFillColor(f,props.settings,props.summary,props.max));
    const radius=(f:GeoFeature)=>pointRadius(f,props.settings,max,instance.getZoom());
    const group=L.geoJSON(props.features as any,{style:f=>({color:color(f as GeoFeature),weight:network?1:.6,fillOpacity:.7}),pointToLayer:(f,latlng)=>L.circleMarker(latlng,{radius:radius(f as GeoFeature),color:color(f as GeoFeature),fillColor:fill(f as GeoFeature),stroke:!network,weight:assets?1.15:1,fillOpacity:assets?1:.8}),onEachFeature:(f,l)=>l.on('click',()=>callbacks.current.onSelect(f as GeoFeature))}).addTo(instance);
    const resizePoints=()=>{group.eachLayer(l=>{if(l instanceof L.CircleMarker)l.setRadius(radius((l as any).feature));});};
    instance.on('zoomend',resizePoints);return()=>{instance.off('zoomend',resizePoints);group.remove();};
  },[props.features,props.settings,props.summary,props.max,max,assets,network]);
  useEffect(()=>{
    const instance=map.current;
    if(!instance || !props.selected) return;
    const marked=props.features.filter(feature=>featureId(feature)===props.selected);
    const radius=(feature:GeoFeature)=>pointRadius(feature,props.settings,max,instance.getZoom())+3.5;
    const rings=L.geoJSON(marked,{
      style:()=>({color:'#16332B',weight:2,fill:false,interactive:false}),
      pointToLayer:(feature,position)=>L.circleMarker(position,{radius:radius(feature),color:'#16332B',weight:2,fill:false,interactive:false}),
    }).addTo(instance);
    const resizeRings=()=>rings.eachLayer(layer=>{
      if(layer instanceof L.CircleMarker) layer.setRadius(radius((layer as L.CircleMarker & {feature:GeoFeature}).feature));
    });
    instance.on('zoomend',resizeRings);
    return ()=>{instance.off('zoomend',resizeRings);rings.remove();};
  },[props.features,props.selected,props.settings,max]);
  useEffect(()=>{if(props.focus&&map.current){const b=props.focus.bounds;map.current.fitBounds([[b[1],b[0]],[b[3],b[2]]],{padding:[30,30],maxZoom:8});}},[props.focus]);
  return <div ref={ref} className="map-host" role="region" aria-label="兼容模式电力地图"/>;
}
