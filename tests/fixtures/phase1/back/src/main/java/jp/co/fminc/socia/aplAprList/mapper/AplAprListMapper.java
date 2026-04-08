package jp.co.fminc.socia.aplAprList.mapper;

import java.util.List;
import java.util.Map;

public interface AplAprListMapper {

    List<Map<String, Object>> selectApplications(Map<String, Object> paramMap);
}
