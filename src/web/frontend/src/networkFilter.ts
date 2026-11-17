import type {GeoFeature,NetworkFilter,Summary} from './types';

export const AUTO_NETWORK:NetworkFilter={mode:'auto'};

export function automaticVoltageFloor(zoom:number) {
  if(zoom<5) return 500;
  if(zoom<7) return 110;
  return 0;
}

export function networkVoltageLevels(features:GeoFeature[],summary:Summary) {
  const colors=new Map(summary.branch_legend?.map(item=>[Number(item.label.split(' ')[0]),item.color]));
  const voltages=new Set<number>();
  for(const feature of features) {
    const voltage=Number(feature.properties.voltage_kv);
    if(Number.isFinite(voltage) && voltage>0) voltages.add(voltage);
  }
  return [...voltages].sort((a,b)=>a-b).map(voltage=>({voltage,color:colors.get(voltage) || '#708880'}));
}

export function visibleNetworkVoltages(available:number[],filter:NetworkFilter,automaticFloor:number) {
  return new Set(filter.mode==='manual' ? filter.voltages : available.filter(voltage=>voltage>=automaticFloor));
}

export function filterNetworkFeatures(features:GeoFeature[],voltages:ReadonlySet<number>) {
  return features.filter(feature=>voltages.has(Number(feature.properties.voltage_kv)));
}
