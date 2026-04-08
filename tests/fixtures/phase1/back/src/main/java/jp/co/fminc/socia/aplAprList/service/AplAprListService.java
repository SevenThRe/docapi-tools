package jp.co.fminc.socia.aplAprList.service;

import java.util.HashMap;
import java.util.List;
import java.util.Map;

import jp.co.fminc.socia.aplAprList.mapper.AplAprListMapper;

public class AplAprListService {

    private AplAprListMapper aplAprListMapper;

    public Map<String, Object> show(Map<String, Object> paramMap) {
        paramMap.get("functionId");
        paramMap.get("menuId");
        List<Map<String, Object>> applications = aplAprListMapper.selectApplications(paramMap);

        Map<String, Object> result = new HashMap<>();
        result.put("syainApplicationsList", applications);
        result.put("status", "ok");
        return result;
    }
}
