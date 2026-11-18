import type {Geometry,Position} from 'geojson';
import type {GeoFeature,Payload,Prepared,LayerId,MapSettings} from './types';
export function boundsOf(geometry:Geometry):[number,number,number,number]{
  const bounds:[number,number,number,number]=[Infinity,Infinity,-Infinity,-Infinity];
  const walk=(value:any)=>{if(typeof value?.[0]==='number'){if(Number.isFinite(value[0])&&Number.isFinite(value[1])){bounds[0]=Math.min(bounds[0],value[0]);bounds[1]=Math.min(bounds[1],value[1]);bounds[2]=Math.max(bounds[2],value[0]);bounds[3]=Math.max(bounds[3],value[1]);}}else for(const part of value||[])walk(part);};
  const visit=(g:Geometry)=>{if(g.type==='GeometryCollection')g.geometries.forEach(visit);else walk(g.coordinates);};visit(geometry);return bounds;
}
export function positionOf(geometry:Geometry):Position{if(geometry.type==='Point')return geometry.coordinates;const b=boundsOf(geometry);return[(b[0]+b[2])/2,(b[1]+b[3])/2];}
export function partsOf(geometry:Geometry):Geometry[]{return geometry.type==='GeometryCollection'?geometry.geometries.flatMap(partsOf):[geometry];}
export function prepare(payload:Payload,layer:LayerId):Prepared{
  const features:GeoFeature[]=[];let max=0,min=Infinity;const started=performance.now();
  payload.geojson.features.forEach((source,index)=>{
    if(!source.geometry)return;
    const p=source.properties;const id=String(p.location_uid??p.spatial_uid??p.uid??source.id??index);
    const value=Number(p.total_mw??p.raw_value??p.value??0);const colorValue=Number(p.value??value);
    if(!Number.isFinite(value)||!Number.isFinite(colorValue))return;
    max=Math.max(max,colorValue);min=Math.min(min,colorValue);
    // Keep source identity when GeometryCollections split; quantities are counted once.
    const allParts=partsOf(source.geometry);
    // Area layers can contain zero-area boundary remnants from geometry repair.
    // Render the area once; do not turn those remnants into duplicate quantity markers.
    const hasArea=allParts.some(g=>g.type==='Polygon'||g.type==='MultiPolygon');
    const parts=hasArea&&['load','resource','population'].includes(layer)?allParts.filter(g=>g.type==='Polygon'||g.type==='MultiPolygon'):allParts;
    parts.forEach((part,partIndex)=>{
      const geometry=layer==='load'&&!['Polygon','MultiPolygon'].includes(part.type)?{type:'Point' as const,coordinates:positionOf(part)}:part;
      features.push({type:'Feature',id:`${index}:${partIndex}`,geometry,properties:{...p,__id:id,__index:index,__value:value,__colorValue:colorValue,__position:positionOf(geometry)}});
    });
  });
  return{features,summary:payload.summary,sourceCount:payload.geojson.features.length,max,min:Number.isFinite(min)?min:0,elapsed:performance.now()-started};
}
export function aggregatePoints(features:GeoFeature[],size:number):GeoFeature[]{
  const cells=new Map<string,GeoFeature>();
  for(const feature of features){const [x,y]=feature.properties.__position;const key=`${Math.floor(x/size)}:${Math.floor(y/size)}`;const existing=cells.get(key);
    if(existing){const p=existing.properties;p.__value+=feature.properties.__value;p.total_mw=p.__value;p.__count++;for(const [name,value] of Object.entries(feature.properties.breakdown||{}))p.breakdown[name]=(p.breakdown[name]||0)+Number(value);}
    else cells.set(key,{type:'Feature',geometry:{type:'Point',coordinates:[(Math.floor(x/size)+.5)*size,(Math.floor(y/size)+.5)*size]},properties:{...feature.properties,__id:`aggregate:${key}`,__position:[(Math.floor(x/size)+.5)*size,(Math.floor(y/size)+.5)*size],__count:1,breakdown:{...feature.properties.breakdown}}});
  }return [...cells.values()];
}
export function isStandardGeneration(settings:Pick<MapSettings,'stage'|'layer'>) {
  return settings.stage==='standard' && settings.layer==='generator';
}

export function assetDisplayFeatures(features:GeoFeature[],settings:Pick<MapSettings,'stage'|'layer'>,zoom:number) {
  const assets=settings.layer==='generator' || settings.layer==='storage';
  if(assets && !isStandardGeneration(settings) && zoom<6 && features.length>1000) {
    return aggregatePoints(features,zoom<4 ? 0.8 : 0.35);
  }
  return features;
}
export function uniqueFeatures(features:GeoFeature[]):GeoFeature[]{const seen=new Set<number|string>();return features.filter(f=>{const id=f.properties.__index??f.properties.__id;if(seen.has(id))return false;seen.add(id);return true;});}
