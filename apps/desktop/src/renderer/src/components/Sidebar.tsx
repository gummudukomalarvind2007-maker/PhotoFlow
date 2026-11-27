import * as React from 'react'
import { navigation } from '../data'
import type { NavigationItem } from '../types'
import { Icon } from './Icon'

interface SidebarProps { active: NavigationItem; onSelect: (item: NavigationItem) => void; retryCount: number }

export function Sidebar({ active, onSelect, retryCount }: SidebarProps) {
  return <aside className="sidebar">
    <div className="brand"><span className="brand-mark"><i /><i /><i /></span><span>PhotoFlow</span></div>
    <nav aria-label="Library navigation">
      <p className="nav-caption">LIBRARY</p>
      {navigation.slice(0, 9).map((item) => <NavButton
        key={item.label}
        {...item}
        badge={item.label === 'Retry Failed' && retryCount ? String(retryCount) : undefined}
        active={active === item.label}
        onSelect={onSelect}
      />)}
      <p className="nav-caption nav-caption-spaced">MANAGE</p>
      {navigation.slice(9).map((item) => <NavButton key={item.label} {...item} active={active === item.label} onSelect={onSelect} />)}
    </nav>
    <div className="sidebar-bottom">
      <div className="library-pill">
        <span className="library-mark" aria-hidden="true"><Icon name="database" size={18} /></span>
        <span><strong>Local library</strong><small>Stored on this device</small></span>
      </div>
    </div>
  </aside>
}

interface NavButtonProps { label: NavigationItem; icon: string; badge?: string; active: boolean; onSelect: (item: NavigationItem) => void }
function NavButton({ label, icon, badge, active, onSelect }: NavButtonProps) {
  return <button
    className={`nav-item ${active ? 'active' : ''}`}
    type="button"
    aria-label={label}
    aria-current={active ? 'page' : undefined}
    title={label}
    onClick={() => onSelect(label)}
  >
    <Icon name={icon} />
    <span>{label}</span>
    {badge && <b>{badge}</b>}
  </button>
}
