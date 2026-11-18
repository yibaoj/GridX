import {describe, it, expect} from 'vitest';
import {readPayload} from './contract';

describe('web data contract', () => {
  it('reads existing unversioned packages and explicit v1 packages', async () => {
    for (const payload of [{year: 2024}, {schema_version: 1, year: 2024}]) {
      await expect(readPayload(new Response(JSON.stringify(payload)))).resolves.toEqual(payload);
    }
  });
  it('rejects unsupported versions before pages use the payload', async () => {
    await expect(readPayload(new Response(JSON.stringify({schema_version: 2})))).rejects.toThrow('版本');
  });
});
