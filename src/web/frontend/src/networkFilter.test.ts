import {expect,it} from 'vitest';
import {AUTO_NETWORK,automaticVoltageFloor,filterNetworkFeatures,visibleNetworkVoltages} from './networkFilter';
import type {GeoFeature,NetworkFilter} from './types';

it('uses exact manual voltages at national zoom and restores the automatic floor',()=>{
  const voltages=[35,110,220,500,800];
  const features=voltages.map((voltage):GeoFeature=>({
    type:'Feature',geometry:{type:'LineString',coordinates:[[100,30],[101,30]]},
    properties:{voltage_kv:voltage},
  }));
  const display=(filter:NetworkFilter,zoom=4)=>filterNetworkFeatures(features,
    visibleNetworkVoltages(voltages,filter,automaticVoltageFloor(zoom))).map(feature=>feature.properties.voltage_kv);

  expect(display({mode:'manual',voltages:[500]})).toEqual([500]);
  expect(display({mode:'manual',voltages:[110,220]})).toEqual([110,220]);
  expect(display({mode:'manual',voltages})).toEqual(voltages);
  expect(display({mode:'manual',voltages:[]})).toEqual([]);
  expect(display(AUTO_NETWORK)).toEqual([500,800]);
  expect(display(AUTO_NETWORK,5)).toEqual([110,220,500,800]);
  expect(display(AUTO_NETWORK,7)).toEqual(voltages);
});
