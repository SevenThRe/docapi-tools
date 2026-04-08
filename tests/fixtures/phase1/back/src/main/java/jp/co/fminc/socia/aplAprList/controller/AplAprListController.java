package jp.co.fminc.socia.aplAprList.controller;

import java.util.Map;

import jp.co.fminc.socia.aplAprList.service.AplAprListService;
import org.springframework.web.bind.annotation.GetMapping;
import org.springframework.web.bind.annotation.PostMapping;
import org.springframework.web.bind.annotation.RequestBody;
import org.springframework.web.bind.annotation.RequestMapping;
import org.springframework.web.bind.annotation.RestController;

@RestController
@RequestMapping("/api/aplAprList")
public class AplAprListController {

    private AplAprListService aplAprListService;

    /**
     * 申請詳細を表示する
     */
    @PostMapping("/show")
    public Map<String, Object> show(@RequestBody Map<String, Object> paramMap) {
        return aplAprListService.show(paramMap);
    }

    @GetMapping("/status")
    public String status() {
        return "ok";
    }
}
