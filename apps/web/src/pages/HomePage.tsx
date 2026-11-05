import { useNavigate } from 'react-router-dom'
import homeBackground from '../assets/serene-pond-home-clean.png'
import './HomePage.css'

const leaves = ['leaf-one', 'leaf-two', 'leaf-three']
const motes = ['mote-one', 'mote-two', 'mote-three', 'mote-four']

export default function HomePage() {
  const navigate = useNavigate()

  return (
    <div className="home-page">
      <div className="home-artboard" aria-hidden="true">
        <img className="home-background" src={homeBackground} alt="" />
        <div className="home-ripples"><span /><span /><span /></div>
        {leaves.map((name) => <span key={name} className={`home-leaf ${name}`} />)}
        {motes.map((name) => <span key={name} className={`home-mote ${name}`} />)}
      </div>

      <main className="home-content">
        <h1 className="home-title">欢迎</h1>
        <p className="home-subtitle">一站式完成数据集管理、质量评测与持续整改。</p>
        <button type="button" className="home-enter-btn" onClick={() => navigate('/app')}>
          开始数据质量评测
        </button>
      </main>
    </div>
  )
}
