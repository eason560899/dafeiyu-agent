import { describe, expect, it } from 'vitest'
import { actionView, candidateView } from './api'

describe('API view adapters', () => {
  it('does not invent candidate content', () => {
    const row = candidateView({ id: 'c1', content: '', selected: false })
    expect(row.summary).toBe('')
    expect(row.scoreLabel).toBe('—')
    expect(row.state).toBe('待审核')
  })
  it('maps only returned action status', () => {
    expect(actionView({ id: 'a1', type: 'publish', status: 'pending' }).resultLabel).toBe('等待中')
    expect(actionView({ id: 'a2', type: 'publish', status: 'failed' }).resultLabel).toBe('失败')
  })
})
