import { StrictMode } from 'react';
import { createRoot } from 'react-dom/client';
import './index.css';
import JsonViewerTestApp from './JsonViewerTestApp';

createRoot(document.getElementById('root')!).render(
  <StrictMode>
    <JsonViewerTestApp />
  </StrictMode>,
);

