import {describe,it,expect} from 'vitest';
import {prepare,aggregatePoints,assetDisplayFeatures,uniqueFeatures,boundsOf} from './geometry';
import type {Payload} from './types';
const payload=(features:any[]):Payload=>({summary:{count:features.length},geojson:{type:'FeatureCollection',features}});
describe('source-preserving geographic adapters',()=>{
  it('retains source identity and drops repaired boundary remnants instead of duplicating quantities',()=>{const result=prepare(payload([{type:'Feature',properties:{location_uid:'province:one',value:125},geometry:{type:'GeometryCollection',geometries:[{type:'Polygon',coordinates:[[[1,1],[2,1],[2,2],[1,1]]]},{type:'LineString',coordinates:[[3,3],[4,4]]}]}}]),'load');expect(result.sourceCount).toBe(1);expect(result.features).toHaveLength(1);expect(uniqueFeatures(result.features)).toHaveLength(1);expect(result.features[0].geometry.type).toBe('Polygon');expect(result.features[0].properties.__id).toBe('province:one');});
  it('represents a line-shaped case bus load by its geographic anchor',()=>{const result=prepare(payload([{type:'Feature',properties:{location_uid:'bus:line',value:2},geometry:{type:'LineString',coordinates:[[100,30],[102,32]]}}]),'load');expect(result.features[0].geometry).toEqual({type:'Point',coordinates:[101,31]});});
  it('aggregates point capacity and technology breakdown without changing inputs',()=>{const result=prepare(payload([1,2,3].map((x)=>({type:'Feature',properties:{location_uid:`asset:${x}`,total_mw:x*10,breakdown:{wind:x*10}},geometry:{type:'Point',coordinates:[100+x*.01,30]}}))),'generator');const aggregated=aggregatePoints(result.features,1);expect(aggregated).toHaveLength(1);expect(aggregated[0].properties.__value).toBe(60);expect(aggregated[0].properties.breakdown.wind).toBe(60);expect(aggregated[0].properties.__count).toBe(3);expect(result.features[0].properties.breakdown.wind).toBe(10);});
  it('keeps individual standard generators at their original coordinates in the national view',()=>{
    const source=payload(Array.from({length:1001},(_,index)=>({
      type:'Feature',properties:{location_uid:`gem:${index}`,total_mw:10},
      geometry:{type:'Point',coordinates:[87.7874+index/100000,44.029]},
    })));
    const {features}=prepare(source,'generator');
    const national=assetDisplayFeatures(features,{stage:'standard',layer:'generator'},3.5);
    expect(national).toHaveLength(1001);
    expect(national[0].geometry).toEqual(source.geojson.features[0].geometry);
    expect(national[0].properties.__id).toBe('gem:0');
    expect(assetDisplayFeatures(features,{stage:'mapping',layer:'generator'},3.5)).toHaveLength(1);
  });
  it('keeps population raw numbers separate from logarithmic color values',()=>{const result=prepare(payload([{type:'Feature',properties:{uid:'pop:1',value:3,raw_value:999},geometry:{type:'Point',coordinates:[100,30]}}]),'population');expect(result.features[0].properties.__value).toBe(999);expect(result.max).toBe(3);});
  it('rejects invalid metric values without turning them into a valid zero',()=>{const result=prepare(payload([{type:'Feature',properties:{value:NaN},geometry:{type:'Point',coordinates:[100,30]}}]),'resource');expect(result.features).toHaveLength(0);expect(result.sourceCount).toBe(1);});
  it('calculates bounds through nested mixed geometry',()=>expect(boundsOf({type:'GeometryCollection',geometries:[{type:'Point',coordinates:[73,20]},{type:'LineString',coordinates:[[90,10],[135,54]]}]})).toEqual([73,10,135,54]));
});
