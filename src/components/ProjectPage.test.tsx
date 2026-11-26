import { cleanup, render, screen, waitFor } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { afterEach, expect, test, vi } from 'vitest'
import { downloadProjectFile, getProject, getProjects } from '../api'
import ProjectPage from './ProjectPage'

vi.mock('../api', () => ({ getProjects: vi.fn(), getProject: vi.fn(), downloadProjectFile: vi.fn() }))
afterEach(() => { cleanup(); vi.resetAllMocks() })

test('lists conversations and opens a project', async () => {
  vi.mocked(getProjects).mockResolvedValue({ items: [{ id: 'task-1', title: '天线实验', created_at: '2026-11-17T08:00:00Z', software_id: 'hfss' }] })
  render(<ProjectPage projectId={null} />)
  const link = await screen.findByRole('link', { name: /天线实验/ })
  expect(link).toHaveAttribute('href', '#/projects/task-1')
  expect(link).toHaveTextContent('Ansys HFSS')
  expect(link).toHaveTextContent('实验工作台')
})

test('labels solution projects and shows images without reply text', async () => {
  vi.mocked(getProjects).mockResolvedValue({ items: [{ id: 'solution-1', title: '采样定理', created_at: '2026-11-17T08:00:00Z', software_id: 'solve', kind: 'solution' }] })
  const { rerender } = render(<ProjectPage projectId={null} />)
  const link = await screen.findByRole('link', { name: /采样定理/ })
  expect(link).toHaveTextContent('解题')
  expect(link).toHaveTextContent('生成图片')
  vi.mocked(getProject).mockResolvedValue({ id: 'solution-1', title: '采样定理', created_at: '2026-11-17T08:00:00Z', software_id: 'solve', kind: 'solution', guide: null,
    messages: [{ message_id: 'm1', created_at: '2026-11-17T08:00:00Z', input_preview: '画波形',
      inputs: [{ name: 'problem.png', path: 'messages/m1/attachment-1.png', size: 100 }],
      outputs: [{ name: '20261117_080000_解题_图片_01.png', path: 'solutions/m1/function.png', size: 100, kind: 'image' }],
    }] })
  rerender(<ProjectPage projectId="solution-1" />)
  expect(await screen.findByText('画波形')).toBeInTheDocument()
  expect(screen.queryByText(/解答文本/)).not.toBeInTheDocument()
  expect(screen.getByRole('button', { name: /problem.png/ })).toBeInTheDocument()
  expect(screen.getByRole('button', { name: /解题_图片_01.png/ })).toBeInTheDocument()
  expect(screen.queryByRole('heading', { name: '指导文件' })).not.toBeInTheDocument()
})

test('shows inputs and outputs per message and downloads a file', async () => {
  vi.mocked(getProject).mockResolvedValue({ id: 'task-1', title: '天线实验', created_at: '2026-11-17T08:00:00Z', software_id: 'hfss',
    guide: { name: 'guide.pdf', path: 'messages/m1/guide.pdf', size: 2048 }, messages: [{
      message_id: 'm1', created_at: '2026-11-17T08:00:00Z', input_preview: '完成天线实验',
      inputs: [{ name: 'task.png', path: 'messages/m1/attachment-1.png', size: 4096 }],
      outputs: [{ name: 'result.hfss', path: 'hfss/result.hfss', size: 8192 }],
    }] })
  vi.mocked(downloadProjectFile).mockResolvedValue()
  render(<ProjectPage projectId="task-1" />)
  expect(await screen.findByText('完成天线实验')).toBeInTheDocument()
  const backLink = screen.getByRole('link', { name: '返回项目列表' })
  expect(backLink).toHaveAttribute('href', '#/projects')
  expect(backLink).toHaveClass('projects-page__back')
  expect(screen.getByRole('button', { name: /guide.pdf/ })).toBeInTheDocument()
  await userEvent.setup().click(screen.getByRole('button', { name: /result.hfss/ }))
  await waitFor(() => expect(downloadProjectFile).toHaveBeenCalledWith('task-1', expect.objectContaining({ path: 'hfss/result.hfss' })))
})

test('uses file type icons and matches source files to the selected software icon', async () => {
  vi.mocked(getProject).mockResolvedValue({ id: 'task-2', title: '图像实验', created_at: '2026-11-17T08:00:00Z', software_id: 'pycharm',
    guide: { name: 'guide.pdf', path: 'messages/m1/guide.pdf', size: 2048 }, messages: [{
      message_id: 'm1', created_at: '2026-11-17T08:00:00Z', input_preview: '处理图片',
      inputs: [
        { name: 'photo.jpeg', path: 'messages/m1/photo.jpeg', size: 4096 },
        { name: 'measurements.xlsx', path: 'messages/m1/measurements.xlsx', size: 4096 },
      ],
      outputs: [
        { name: 'experiment.py', path: 'pycharm/m1/experiment.py', size: 1024, kind: 'code' },
        { name: 'experiment_report.docx', path: 'reports/m1/experiment_report.docx', size: 8192, kind: 'report' },
      ],
    }] })
  render(<ProjectPage projectId="task-2" />)

  const fileIconClass = (name: string) => screen.getByRole('button', { name: new RegExp(name) }).querySelector('svg')?.getAttribute('class')
  await screen.findByText('处理图片')
  expect(fileIconClass('photo.jpeg')).toContain('lucide-file-image')
  expect(fileIconClass('measurements.xlsx')).toContain('lucide-file-spreadsheet')
  expect(fileIconClass('experiment.py')).toContain('lucide-code-xml')
  expect(fileIconClass('experiment_report.docx')).toContain('lucide-file-text')
  expect(fileIconClass('guide.pdf')).toContain('lucide-file-text')
})
