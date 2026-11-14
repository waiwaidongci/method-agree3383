export const roles:Record<string,string>={admin:'实验室负责人',analyst:'测量工程师',viewer:'设备观察员'};
export const statuses:Record<string,string>={queued:'已排队',running:'计算中',completed:'已归档',failed:'未完成',draft:'待复核',approved:'已批准',pending:'待计算',candidate:'候选版本',active:'启用中',superseded:'已归档',discarded:'已作废'};
export const batchStatuses:Record<string,string>={queued:'排队中',running:'整批计算中',ready:'整批就绪 · 待启用',failed:'失败 · 沿用旧结果',revoked:'已撤销',activated:'已启用'};
export const versionStatuses:Record<string,string>={pending:'待计算',candidate:'候选版本',active:'启用中',superseded:'已归档',discarded:'已作废'};
export const modes:Record<string,string>={raw:'原值互换',calibration_only:'仅供校准研究'};
