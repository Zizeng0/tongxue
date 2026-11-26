/** API 和实验工作台共用的错误提示。 */
export const requestText = {
  failed: (status: number) => `请求失败（${status}）`,
  invalidImagePath: '图片路径无效',
  solutionImageFailed: '加载题解图片失败',
  questionImageFailed: '加载题目图片失败',
  projectDownloadFailed: (status: number) => `下载文件失败（${status}）`,
  reportDownloadFailed: (status: number) => `下载报告失败（${status}）`,
} as const

export const workspaceText = {
  archiveFailed: '归档会话失败。',
  guideAlreadySet: '指导文件只能在新会话首次上传。',
  guideType: '任务指导书仅支持 PDF 文件。',
  guideSize: '指导文件不能超过 120 MB。',
  attachmentType: '任务附件仅支持图片或 PDF 文件。',
  attachmentSize: 'PDF 附件不能超过 100 MB，图片附件不能超过 25 MB。',
  templateType: '实验报告模板仅支持 DOC 或 DOCX 文件。',
  templateSize: '实验报告模板不能超过 10 MB。',
  fileType: '其他文件格式不受支持。',
  fileSize: '其他文件过大：图片不能超过 25 MB，其他文件不能超过 100 MB。',
  softwareSaveFailed: '软件选择保存失败。',
  guideRequired: '请先上传任务指导书 PDF。',
  requirementsRequired: '请填写要求。',
  deliverablesRequired: '请至少选择一种交付成果。',
  draftSaveFailed: '任务草稿保存失败。',
  reportDownloadFailed: '下载报告失败。',
} as const
