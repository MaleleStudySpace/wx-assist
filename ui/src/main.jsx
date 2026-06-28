import React from 'react'
import ReactDOM from 'react-dom/client'
import App from './App'
import './index.css'

// --- console easter egg ---
;(function () {
  const c = 'color:#346538;font-size:14px;'
  const b = 'color:#346538;font-size:18px;font-weight:bold;'
  const n = 'color:#787774;font-size:12px;'
  console.log('%c🚣%c  微信助手 %c— wx-assist', b, b, n)
  console.log('%c  微信助手，轻松管理。', c)
})()

ReactDOM.createRoot(document.getElementById('root')).render(
  <React.StrictMode>
    <App />
  </React.StrictMode>
)
