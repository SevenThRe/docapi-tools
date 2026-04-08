package jp.co.fminc.socia.aplAprList.controller;

import org.springframework.web.bind.annotation.PostMapping;
import org.springframework.web.bind.annotation.RequestMapping;
import org.springframework.web.bind.annotation.RestController;

@RestController
@RequestMapping(API_ROOT)
public class ConstantPathController {

    private static final String API_ROOT = "/api/aplAprList";
    private static final String DETAIL_PATH = "/zzpreview";

    @PostMapping(DETAIL_PATH)
    public String preview() {
        return "ok";
    }
}
