import {defineConfig} from 'vite';
import react from '@vitejs/plugin-react';

export default defineConfig({
  plugins:[react()],
  build:{outDir:'../../../outputs/web-ui',emptyOutDir:true,chunkSizeWarningLimit:1700},
  server:{proxy:{'/api':process.env.GRIDX_API_URL||'http://127.0.0.1:8765'}},
});
