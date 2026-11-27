import { Icon } from './Icon'
interface StatCardProps { icon: string; tone: 'purple' | 'blue' | 'orange'; label: string; value: string; trend: string }
export function StatCard({ icon, tone, label, value, trend }: StatCardProps) { return <article className="stat-card"><div className={`stat-icon ${tone}`}><Icon name={icon} /></div><p>{label}</p><strong>{value}</strong><small>{trend}</small></article> }
