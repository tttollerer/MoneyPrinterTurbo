import {test} from 'node:test';
import assert from 'node:assert/strict';
import {errorMessage,assetUrl} from '../src/api.js';
test('FastAPI validation errors expose the field and useful explanation',()=>assert.equal(errorMessage([{loc:['body','audio','music_gain'],msg:'must be <= 1'}]),'body.audio.music_gain: must be <= 1'));
test('asset identifiers are URL encoded',()=>assert.equal(assetUrl('a/b'),'\/api/assets/a%2Fb/file'));
