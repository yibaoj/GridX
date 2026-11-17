import type {GeoFeature,MapSettings,Summary} from './types';
import {isStandardGeneration} from './geometry';
export const rgb=(hex:string):[number,number,number,number]=>{const v=hex.replace('#','');return[parseInt(v.slice(0,2),16)||0,parseInt(v.slice(2,4),16)||0,parseInt(v.slice(4,6),16)||0,225];};
// Presentation overrides only: imported category color fields remain untouched.
const energyPalette:Record<string,{stroke:string;fill:string}>={
  solar:{stroke:'#996400',fill:'#FFDF85'},wind:{stroke:'#087F88',fill:'#BBE7E8'},
  coal:{stroke:'#4C5564',fill:'#D5D9E0'},bioenergy:{stroke:'#397B31',fill:'#C9E6BA'},
  gas:{stroke:'#C15115',fill:'#FFC394'},other:{stroke:'#704532',fill:'#DEC4AF'},
  hydropower:{stroke:'#2769BA',fill:'#C5DDFF'},nuclear:{stroke:'#B62D60',fill:'#F8C1D4'},
  geothermal:{stroke:'#773CB0',fill:'#E2CCF7'},
  pumped_storage:{stroke:'#2769BA',fill:'#C5DDFF'},battery_storage:{stroke:'#087F88',fill:'#BBE7E8'},
  compressed_air_storage:{stroke:'#773CB0',fill:'#E2CCF7'},thermal_storage:{stroke:'#C15115',fill:'#FFC394'},
  capacitor_storage:{stroke:'#996400',fill:'#FFDF85'}
};
export const energyStyle=(id:string,fallback='#668D7B')=>energyPalette[id]||{stroke:fallback,fill:'#E0E9E3'};
const energyId=(f:GeoFeature,settings:MapSettings)=>settings.category||Object.entries(f.properties.breakdown||{}).sort((a,b)=>Number(b[1])-Number(a[1]))[0]?.[0]||'';
export function pointFillColor(f:GeoFeature,settings:MapSettings,summary:Summary,max:number):[number,number,number,number]{
  if(settings.layer!=='generator'&&settings.layer!=='storage')return featureColor(f,settings,summary,max);
  const id=energyId(f,settings);const value=rgb(energyStyle(id,summary.categories?.find(c=>c.id===id)?.color).fill);value[3]=255;return value;
}
export function pointRadius(f:GeoFeature,settings:MapSettings,max:number,zoom:number){
  if(settings.layer==='network')return zoom<5?0.9:zoom<7?1.3:2;
  if(isStandardGeneration(settings) && zoom<6) {
    return 1.8+Math.sqrt(Math.max(0,f.properties.__value)/Math.max(max,1))*3;
  }
  if(settings.layer==='generator'||settings.layer==='storage')return (zoom<6?2.8:3.5)+Math.sqrt(Math.max(0,f.properties.__value)/Math.max(max,1))*(zoom<6?4.2:6);
  return Math.min(10,1.5+Math.sqrt(Math.max(0,f.properties.__value)/Math.max(max,1))*8);
}
export function featureColor(f:GeoFeature,settings:MapSettings,summary:Summary,max:number):[number,number,number,number]{
  const p=f.properties;
  if(settings.layer==='network')return rgb(summary.branch_legend?.find(c=>c.label===p.style)?.color||(p.node_type==='station'?'#bf5163':'#708880'));
  if(settings.layer==='generator'||settings.layer==='storage'){
    const id=energyId(f,settings);
    const color=rgb(energyStyle(id,summary.categories?.find(c=>c.id===id)?.color).stroke);color[3]=255;return color;
  }
  const ratio=Math.max(0,Math.min(1,p.__colorValue/Math.max(max,1e-12)));
  const a=settings.layer==='resource'?[221,237,222]:[224,240,235];const b=settings.layer==='resource'?[39,112,99]:[24,115,86];
  return[a[0]+(b[0]-a[0])*ratio,a[1]+(b[1]-a[1])*ratio,a[2]+(b[2]-a[2])*ratio,220];
}
