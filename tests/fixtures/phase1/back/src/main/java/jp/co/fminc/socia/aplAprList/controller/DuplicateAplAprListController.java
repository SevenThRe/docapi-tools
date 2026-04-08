package jp.co.fminc.socia.aplAprList.controller;

import org.springframework.web.bind.annotation.PostMapping;
import org.springframework.web.bind.annotation.RequestMapping;
import org.springframework.web.bind.annotation.RestController;

@RestController
@RequestMapping("/api/aplAprList")
public class DuplicateAplAprListController {

    /**
     * 重複した show エンドポイント
     */
    @PostMapping("/show")
    public String show() {
        return "duplicate";
    }
}
