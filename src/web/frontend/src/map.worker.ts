import {prepare} from './geometry';
import type {MapSettings} from './types';
self.onmessage=async({data:settings}:{data:MapSettings})=>{
  try{const suffix=settings.layer==='resource'?`/${settings.resource}`:'';
    const response=await fetch(`/api/layers/${settings.stage}/${settings.layer}${suffix}`);
    if(!response.ok)throw new Error(`图层暂不可用（${response.status}），请检查数据包后重试。`);
    self.postMessage(prepare(await response.json(),settings.layer));
  }catch(error){self.postMessage({error:error instanceof Error?error.message:'图层加载失败'});}
};
